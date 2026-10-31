"""Decoded stored records reused across chat turns (get_record_from_storage)."""

import asyncio
import json
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.transformers import blob_storage as bs_mod
from app.modules.transformers.blob_storage import BlobStorage, _DecodedRecordCache
from app.utils import turn_memo

LOOKUP = {"record_doc_id": "doc-1", "fileSizeBytes": 100, "updatedAt": 1700000000000}


def _blob(fetch: AsyncMock) -> BlobStorage:
    blob = BlobStorage(logger=MagicMock(), config_service=MagicMock(), graph_provider=MagicMock())
    blob._get_auth_and_config = AsyncMock(return_value=({}, "http://storage", ""))
    blob._cached_signed_url = AsyncMock(return_value=None)
    blob._fetch_record_envelope = fetch
    return blob


RAW = json.dumps({"record": {"id": "r1", "block_containers": {"blocks": [{"data": "x"}]}}}).encode()


def _fetch(raw: bytes = RAW) -> AsyncMock:
    """The gateway fetch: decodes the body with the decoder it is handed, as the real one does."""
    return AsyncMock(side_effect=lambda session, url, headers, vrid, decode: decode(raw))


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("PIPESHUB_RECORD_CACHE_BYTES", raising=False)
    bs_mod._DECODED_RECORDS.clear()
    with patch.object(bs_mod, "get_shared_session", return_value=MagicMock()):
        yield
    bs_mod._DECODED_RECORDS.clear()


async def _turn(blob: BlobStorage, org_id: str = "org-1", lookup: dict = LOOKUP) -> dict | None:
    async def run() -> dict | None:
        turn_memo.begin_turn()
        return await blob.get_record_from_storage("vr-1", org_id, lookup_result=dict(lookup))
    return await asyncio.create_task(run())


@pytest.mark.asyncio
async def test_later_turns_reuse_the_decoded_record() -> None:
    fetch = _fetch()
    blob = _blob(fetch)
    first = await _turn(blob)
    first["id"] = "changed by a caller"
    second = await _turn(blob)
    assert fetch.await_count == 1
    assert second["id"] == "r1"
    assert second is not first


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"updatedAt": 1700000000001},
    {"fileSizeBytes": 101},
    {"record_doc_id": "doc-2"},
])
async def test_a_rewritten_record_is_downloaded_again(change: dict) -> None:
    fetch = _fetch()
    blob = _blob(fetch)
    await _turn(blob)
    await _turn(blob, lookup={**LOOKUP, **change})
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_org_is_part_of_the_key() -> None:
    fetch = _fetch()
    blob = _blob(fetch)
    await _turn(blob, "org-1")
    await _turn(blob, "org-2")
    assert fetch.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup", [
    {"record_doc_id": "doc-1", "fileSizeBytes": 100},
    {"record_doc_id": "doc-1", "updatedAt": 1},
])
async def test_a_mapping_that_cannot_tell_versions_apart_is_not_cached(lookup: dict) -> None:
    fetch = _fetch()
    blob = _blob(fetch)
    await _turn(blob, lookup=lookup)
    await _turn(blob, lookup=lookup)
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_outside_a_chat_turn_every_call_downloads() -> None:
    fetch = _fetch()
    blob = _blob(fetch)
    await blob.get_record_from_storage("vr-1", "org-1", lookup_result=LOOKUP)
    await blob.get_record_from_storage("vr-1", "org-1", lookup_result=LOOKUP)
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_zero_bytes_disables_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_RECORD_CACHE_BYTES", "0")
    fetch = _fetch()
    blob = _blob(fetch)
    await _turn(blob)
    await _turn(blob)
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_a_failed_download_is_not_cached() -> None:
    fetch = AsyncMock(side_effect=[RuntimeError("storage down"), {"record": {"id": "r1"}}])
    blob = _blob(fetch)
    with pytest.raises(RuntimeError):
        await _turn(blob)
    assert (await _turn(blob))["id"] == "r1"


def test_entries_expire() -> None:
    now = [0.0]
    cache = _DecodedRecordCache(60.0, clock=lambda: now[0])
    cache.put(("k",), {"a": 1}, 10, 1000)
    now[0] = 59.9
    assert cache.get(("k",)) == {"a": 1}
    now[0] = 60.0
    assert cache.get(("k",)) is None


def test_least_recently_used_goes_first_when_over_budget() -> None:
    cache = _DecodedRecordCache(60.0)
    for key in "abcd":
        cache.put((key,), {}, 25, 100)
    cache.get(("a",))
    cache.put(("e",), {}, 25, 100)
    assert cache.get(("b",)) is None
    assert all(cache.get((key,)) == {} for key in "acde")


def test_a_record_over_a_quarter_of_the_budget_is_not_kept() -> None:
    cache = _DecodedRecordCache(60.0)
    cache.put(("a",), {}, 26, 100)
    assert cache.get(("a",)) is None


def test_the_mapping_lookup_carries_updated_at() -> None:
    shaped = BlobStorage._shape_document_lookup(
        {"documentId": "doc-1", "fileSizeBytes": 5, "updatedAt": 42}
    )
    assert shaped == {"record_doc_id": "doc-1", "fileSizeBytes": 5, "updatedAt": 42}
    assert "updatedAt" not in BlobStorage._shape_document_lookup({"documentId": "doc-1"})


@pytest.mark.asyncio
async def test_compressed_records_are_not_cached() -> None:
    """Their stored size says nothing about the decoded size (about 12x here)."""
    fetch = _fetch(json.dumps({"record": "compressed", "isCompressed": True}).encode())
    blob = _blob(fetch)
    blob._process_downloaded_record = MagicMock(return_value={"id": "r1"})
    await _turn(blob)
    await _turn(blob)
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_entries_are_charged_their_decoded_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """Charged body length x _DECODED_BYTES_PER_JSON_BYTE, not the mapping's fileSizeBytes."""
    monkeypatch.setenv("PIPESHUB_RECORD_CACHE_BYTES", str(4 * len(RAW) * bs_mod._DECODED_BYTES_PER_JSON_BYTE - 1))
    fetch = _fetch()
    blob = _blob(fetch)
    await _turn(blob)
    await _turn(blob)
    assert fetch.await_count == 2  # charged one byte over a quarter of the budget


def test_a_huge_budget_still_caps_each_entry() -> None:
    cache = _DecodedRecordCache(60.0)
    cache.put(("k",), {"id": "r"}, bs_mod._RECORD_CACHE_MAX_ENTRY_BYTES + 1, max_bytes=1 << 40)
    assert cache.get(("k",)) is None


@pytest.mark.asyncio
async def test_a_record_without_a_mapping_is_not_looked_up_twice() -> None:
    blob = _blob(_fetch())
    blob.get_document_id_by_virtual_record_id = AsyncMock(return_value=None)
    assert await blob.get_record_from_storage("vr-1", "org-1") is None
    assert blob.get_document_id_by_virtual_record_id.await_count == 1
