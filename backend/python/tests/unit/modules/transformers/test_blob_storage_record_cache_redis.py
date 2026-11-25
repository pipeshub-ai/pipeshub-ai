"""The stored-record cache kept in Redis (PIPESHUB_RECORD_CACHE_BACKEND=redis) and the cache size cap."""

import asyncio
import json
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.transformers import blob_storage as bs_mod
from app.modules.transformers.blob_storage import BlobStorage, record_cache_bytes
from app.utils import turn_memo

LOOKUP = {"record_doc_id": "doc-1", "fileSizeBytes": 100, "updatedAt": 1700000000000}
RAW = json.dumps({"record": {"id": "r1", "record_name": "Doc", "block_containers": {"blocks": [{"data": "x"}]}}}).encode()


class FakeRedis:
    def __init__(self, *, fail_get: bool = False, fail_set: bool = False) -> None:
        self.data: dict[str, bytes] = {}
        self.ttl: dict[str, int] = {}
        self.fail_get = fail_get
        self.fail_set = fail_set

    async def get(self, key: str) -> bytes | None:
        if self.fail_get:
            raise ConnectionError("redis down")
        return self.data.get(key)

    async def set(self, key: str, value: bytes, ex: int) -> None:
        if self.fail_set:
            raise ConnectionError("redis down")
        self.data[key] = value
        self.ttl[key] = ex


def _fetch(raw: bytes = RAW) -> AsyncMock:
    return AsyncMock(side_effect=lambda session, url, headers, vrid, decode: decode(raw))


def _blob(fetch: AsyncMock) -> BlobStorage:
    blob = BlobStorage(logger=MagicMock(), config_service=MagicMock(), graph_provider=MagicMock())
    blob._get_auth_and_config = AsyncMock(return_value=({}, "http://storage", ""))
    blob._cached_signed_url = AsyncMock(return_value=None)
    blob._fetch_record_envelope = fetch
    return blob


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("PIPESHUB_RECORD_CACHE_BYTES", raising=False)
    monkeypatch.setenv("PIPESHUB_RECORD_CACHE_BACKEND", "redis")
    bs_mod._DECODED_RECORDS.clear()
    with patch.object(bs_mod, "get_shared_session", return_value=MagicMock()):
        yield
    bs_mod._DECODED_RECORDS.clear()


def _with_redis(client: FakeRedis | None) -> "patch":
    value = None if client is None else (client, "ns:reccache:")
    return patch.object(bs_mod, "_record_redis_client", AsyncMock(return_value=value))


async def _turn(blob: BlobStorage, lookup: dict = LOOKUP, *, head_only: bool = False) -> dict | None:
    async def run() -> dict | None:
        turn_memo.begin_turn()
        return await blob.get_record_from_storage("vr-1", "org-1", lookup_result=dict(lookup), head_only=head_only)
    return await asyncio.create_task(run())


@pytest.mark.asyncio
async def test_memory_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PIPESHUB_RECORD_CACHE_BACKEND")
    fetch = _fetch()
    blob = _blob(fetch)
    with _with_redis(FakeRedis()) as client_for:
        await _turn(blob)
        await _turn(blob)
    client_for.assert_not_awaited()
    assert fetch.await_count == 1
    assert len(bs_mod._DECODED_RECORDS._entries) == 1


@pytest.mark.asyncio
async def test_a_miss_stores_the_body_and_a_later_turn_reads_it_from_redis() -> None:
    redis = FakeRedis()
    fetch = _fetch()
    blob = _blob(fetch)
    with _with_redis(redis):
        first = await _turn(blob)
        first["id"] = "changed by a caller"
        second = await _turn(blob)
    assert fetch.await_count == 1
    assert second["id"] == "r1"
    (key,) = redis.data
    assert key.startswith("ns:reccache:org-1|vr-1|doc-1|")
    assert redis.data[key] == RAW
    assert redis.ttl[key] == 60
    assert not bs_mod._DECODED_RECORDS._entries  # nothing kept in process


@pytest.mark.asyncio
async def test_a_new_version_of_the_record_misses() -> None:
    redis = FakeRedis()
    fetch = _fetch()
    blob = _blob(fetch)
    with _with_redis(redis):
        await _turn(blob)
        await _turn(blob, {**LOOKUP, "updatedAt": LOOKUP["updatedAt"] + 1})
    assert fetch.await_count == 2
    assert len(redis.data) == 2


@pytest.mark.asyncio
async def test_a_head_read_stores_the_whole_body_for_full_reads() -> None:
    redis = FakeRedis()
    fetch = _fetch()
    blob = _blob(fetch)
    with _with_redis(redis):
        head = await _turn(blob, head_only=True)
        full = await _turn(blob)
        head_again = await _turn(blob, head_only=True)
    assert fetch.await_count == 1
    assert "block_containers" not in head and head["record_name"] == "Doc"
    assert full["block_containers"] == {"blocks": [{"data": "x"}]}
    assert "block_containers" not in head_again


@pytest.mark.asyncio
@pytest.mark.parametrize("redis", [None, FakeRedis(fail_get=True), FakeRedis(fail_set=True)])
async def test_redis_errors_fall_back_to_the_download(redis: FakeRedis | None) -> None:
    fetch = _fetch()
    blob = _blob(fetch)
    with _with_redis(redis):
        first = await _turn(blob)
        second = await _turn(blob)
    assert first["id"] == second["id"] == "r1"
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_a_body_over_the_entry_cap_is_not_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bs_mod, "_REDIS_RECORD_MAX_BYTES", len(RAW) - 1)
    redis = FakeRedis()
    blob = _blob(_fetch())
    with _with_redis(redis):
        assert (await _turn(blob))["id"] == "r1"
    assert not redis.data


@pytest.mark.asyncio
async def test_compressed_records_are_not_stored() -> None:
    redis = FakeRedis()
    blob = _blob(_fetch(json.dumps({"record": "compressed", "isCompressed": True}).encode()))
    blob._process_downloaded_record = MagicMock(return_value={"id": "r1"})
    with _with_redis(redis):
        await _turn(blob)
    assert not redis.data


@pytest.mark.asyncio
async def test_zero_bytes_turns_the_redis_cache_off_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_RECORD_CACHE_BYTES", "0")
    fetch = _fetch()
    blob = _blob(fetch)
    with _with_redis(FakeRedis()) as client_for:
        await _turn(blob)
        await _turn(blob)
    client_for.assert_not_awaited()
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_outside_a_chat_turn_redis_is_not_used() -> None:
    fetch = _fetch()
    blob = _blob(fetch)
    with _with_redis(FakeRedis()) as client_for:
        await blob.get_record_from_storage("vr-1", "org-1", lookup_result=dict(LOOKUP))
    client_for.assert_not_awaited()


@pytest.mark.parametrize(("value", "expected"), [
    (None, 512 * 1024 * 1024),
    ("", 512 * 1024 * 1024),
    ("abc", 512 * 1024 * 1024),
    (str(2 * 1024 ** 3), 512 * 1024 * 1024),
    ("1048576", 1048576),
    ("0", 0),
    ("-5", 0),
])
def test_cache_size_is_capped_at_512_mib(monkeypatch: pytest.MonkeyPatch, value: str | None, expected: int) -> None:
    if value is None:
        monkeypatch.delenv("PIPESHUB_RECORD_CACHE_BYTES", raising=False)
    else:
        monkeypatch.setenv("PIPESHUB_RECORD_CACHE_BYTES", value)
    assert record_cache_bytes() == expected


@pytest.mark.asyncio
async def test_unreachable_redis_is_tried_once_per_loop_and_its_client_closed() -> None:
    client = MagicMock()
    client.ping = AsyncMock(side_effect=ConnectionError("refused"))
    client.aclose = AsyncMock()
    provider = MagicMock(key_namespace="", create_client=MagicMock(return_value=client))
    config = MagicMock(get_redis_config=AsyncMock(return_value=MagicMock(host="h", port=1, password="", db=0, tls=False)))
    logger = MagicMock()
    with patch.object(bs_mod, "get_redis_provider", return_value=provider):
        assert await bs_mod._record_redis_client(config, logger) is None
        assert await bs_mod._record_redis_client(config, logger) is None
    assert provider.create_client.call_count == 1
    client.aclose.assert_awaited_once()
    logger.warning.assert_called_once()
    bs_mod._record_redis.pop(asyncio.get_running_loop(), None)


@pytest.mark.asyncio
async def test_shutdown_closes_the_record_cache_client() -> None:
    client = MagicMock(aclose=AsyncMock())
    bs_mod._record_redis[asyncio.get_running_loop()] = (client, "reccache:")
    await bs_mod.close_shared_redis()
    client.aclose.assert_awaited_once()
    assert asyncio.get_running_loop() not in bs_mod._record_redis


@pytest.mark.asyncio
async def test_an_unreadable_redis_value_falls_back_to_the_download() -> None:
    redis = FakeRedis()
    fetch = _fetch()
    blob = _blob(fetch)
    with _with_redis(redis):
        await _turn(blob)
        (key,) = redis.data
        redis.data[key] = b"\xff not json"
        assert (await _turn(blob))["id"] == "r1"
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_the_memory_backend_keeps_only_the_body_length(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PIPESHUB_RECORD_CACHE_BACKEND")
    blob = _blob(_fetch())
    sizing: dict = {}
    await blob._read_stored_document("org-1", "vr-1", "doc-1", 100, {}, "http://storage", sizing=sizing)
    assert sizing == {"bytes": len(RAW)}


@pytest.mark.asyncio
async def test_a_signed_url_read_gets_the_plain_decoder() -> None:
    """Gateways for S3/Azure answer with a signed URL; that body is never cached, so it is not kept."""
    blob = _blob(AsyncMock(return_value={"signedUrl": "https://bucket/x"}))
    blob._store_signed_url = AsyncMock()
    blob._record_from_signed_url = AsyncMock(return_value={"id": "r1"})
    sizing: dict = {"keep_raw": True}
    await blob._read_stored_document("org-1", "vr-1", "doc-1", 100, {}, "http://storage", sizing=sizing)
    assert blob._record_from_signed_url.await_args.kwargs["decode"] is bs_mod._decode_json
    assert "raw" not in sizing
