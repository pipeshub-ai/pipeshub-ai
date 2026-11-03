"""Reading a large stored record is gated, taken off the event loop, and capped.

Decoding a record holds ~2.4x its msgpack size as Python objects, and inline it
freezes the event loop for the whole decode. Records over LARGE_DECODE_BYTES
(read from the zstd frame header, before any decode) go through one slot per
event loop and decode in a thread; records over the ceiling are refused. Small
and uncompressed records must keep the inline path they had.

LARGE_DECODE_BYTES is patched down to a few hundred bytes so real envelopes
built by ``_compress_record`` exercise the gate without megabytes of test data.
"""

import asyncio
import base64
import gc
import json
import struct
import threading
import time
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import msgspec
import pytest
import zstandard as zstd

from app.exceptions.storage_exceptions import (
    StoredRecordTooLargeError,
)
from app.modules.transformers import blob_storage as bs_mod
from app.modules.transformers.blob_storage import (
    BlobStorage,
    _compressed_content_size,
    _decode_slot_for,
    max_decoded_record_bytes,
)

LARGE = 512


@pytest.fixture(autouse=True)
def _small_gate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(bs_mod, "LARGE_DECODE_BYTES", LARGE)
    monkeypatch.delenv(bs_mod._MAX_DECODED_BYTES_ENV, raising=False)
    monkeypatch.delenv(bs_mod._COMPRESSION_THRESHOLD_ENV, raising=False)
    bs_mod._decode_slots.clear()
    yield
    bs_mod._decode_slots.clear()


def _bs() -> BlobStorage:
    return BlobStorage(logger=MagicMock(), config_service=MagicMock(), graph_provider=MagicMock())


def _record(size: int) -> dict:
    return {"record_name": "deck", "blocks": [{"type": "text", "data": "x" * size}]}


def _msgpack_len(record: dict) -> int:
    return len(msgspec.msgpack.encode(record))


def _envelope(record: dict) -> dict:
    """As a reader receives it: the envelope's JSON carries the payload as text."""
    payload = _bs()._compress_record(record).decode("ascii")
    return {"isCompressed": True, "record": payload, "virtualRecordId": "vr-1"}


def _slot_taken() -> bool:
    return asyncio.get_running_loop() in bs_mod._decode_slots


class _Blocking:
    """Stands in for ``_decompressed_payload``, the decode thread's work; parks each call until released."""

    def __init__(self, result: dict | None = None, error: Exception | None = None) -> None:
        self.result = result if result is not None else {"ok": True}
        self.error = error
        self.inside = 0
        self.max_inside = 0
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()

    def __call__(self, data: dict) -> bytes:
        with self._lock:
            self.calls += 1
            self.inside += 1
            self.max_inside = max(self.max_inside, self.inside)
        self.entered.set()
        try:
            assert self.release.wait(5), "test never released the decode"
            if self.error is not None:
                raise self.error
            return msgspec.msgpack.encode(self.result)
        finally:
            with self._lock:
                self.inside -= 1


async def _until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached"
        await asyncio.sleep(0.005)


class TestContentSizeProbe:
    def test_reads_the_decoded_size_from_the_header(self) -> None:
        record = _record(5000)
        assert _compressed_content_size(_envelope(record)) == _msgpack_len(record)

    @pytest.mark.parametrize(
        "data",
        [
            {"isCompressed": False, "record": {"a": 1}},
            {"record": {"a": 1}},
            {"isCompressed": True},
            {"isCompressed": True, "record": ""},
            {"isCompressed": True, "record": {"not": "a string"}},
            {"isCompressed": True, "record": 12345},
            {"isCompressed": True, "record": "abc"},
            {"isCompressed": True, "record": "!!!!not base64 at all!!!!"},
            {"isCompressed": True, "record": base64.b64encode(b"plainly not a zstd frame").decode()},
        ],
    )
    def test_unreadable_or_uncompressed_is_none(self, data: dict) -> None:
        assert _compressed_content_size(data) is None

    def test_streamed_frame_reports_unknown(self) -> None:
        obj = zstd.ZstdCompressor().compressobj()
        frame = obj.compress(msgspec.msgpack.encode(_record(5000))) + obj.flush()
        data = {"isCompressed": True, "record": base64.b64encode(frame).decode()}
        assert _compressed_content_size(data) == zstd.CONTENTSIZE_UNKNOWN


class TestCeilingConfig:
    def test_off_by_default(self) -> None:
        """A refused record drops out of every answer silently, so nothing is refused unless asked."""
        assert max_decoded_record_bytes() is None

    def test_env_override_is_read_per_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(bs_mod._MAX_DECODED_BYTES_ENV, "1048576")
        assert max_decoded_record_bytes() == 1048576
        monkeypatch.setenv(bs_mod._MAX_DECODED_BYTES_ENV, "2048")
        assert max_decoded_record_bytes() == 2048

    @pytest.mark.parametrize("raw", ["", "lots", "1.5e9", "0", "-1"])
    def test_invalid_or_non_positive_means_no_limit(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        """A bad value must never turn the limit on."""
        monkeypatch.setenv(bs_mod._MAX_DECODED_BYTES_ENV, raw)
        assert max_decoded_record_bytes() is None


class TestInlinePath:
    async def test_uncompressed_record_is_decoded_inline(self) -> None:
        data = {"isCompressed": False, "record": {"a": 1}}
        assert await _bs()._decode_record(data) == {"a": 1}
        assert not _slot_taken()

    async def test_small_compressed_record_is_decoded_inline(self) -> None:
        record = _record(10)
        assert _msgpack_len(record) <= LARGE
        assert await _bs()._decode_record(_envelope(record)) == record
        assert not _slot_taken()

    async def test_empty_record_is_inline(self) -> None:
        assert await _bs()._decode_record(_envelope({})) == {}
        assert not _slot_taken()

    async def test_size_exactly_at_the_gate_is_inline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        record = _record(2000)
        monkeypatch.setattr(bs_mod, "LARGE_DECODE_BYTES", _msgpack_len(record))
        assert await _bs()._decode_record(_envelope(record)) == record
        assert not _slot_taken()

    async def test_one_byte_over_the_gate_is_gated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        record = _record(2000)
        monkeypatch.setattr(bs_mod, "LARGE_DECODE_BYTES", _msgpack_len(record) - 1)
        assert await _bs()._decode_record(_envelope(record)) == record
        assert _slot_taken()

    async def test_compress_everything_keeps_a_chat_turn_inline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Threshold 0 compresses every record; none may queue on the one slot."""
        monkeypatch.setattr(bs_mod, "LARGE_DECODE_BYTES", 20 * 1024 * 1024)
        monkeypatch.setenv(bs_mod._COMPRESSION_THRESHOLD_ENV, "0")
        storage = _bs()
        records = [{"record_name": f"r{i}", "blocks": [{"data": f"text {i}"}]} for i in range(120)]
        envelopes = []
        for record in records:
            body, compressed = storage._upload_body(record, "vr-1")
            assert compressed
            envelopes.append(json.loads(body))

        executor = MagicMock()
        executor.submit.side_effect = AssertionError("went to a thread")
        with patch.object(bs_mod, "_DECODE_EXECUTOR", executor):
            decoded = await asyncio.gather(*(storage._decode_record(e) for e in envelopes))

        assert decoded == records
        assert not _slot_taken()


class TestUnchangedErrors:
    """An unreadable header falls back to the inline decode and its existing errors."""

    @pytest.mark.parametrize(
        "data",
        [
            {"isCompressed": True, "record": "!!!!not base64 at all!!!!"},
            {"isCompressed": True, "record": base64.b64encode(b"plainly not a zstd frame").decode()},
            {"isCompressed": True, "record": {"not": "a string"}},
            {"isCompressed": True},
            {"isCompressed": False},
        ],
    )
    async def test_same_error_as_process_downloaded_record(self, data: dict) -> None:
        storage = _bs()
        with pytest.raises(Exception) as direct:
            storage._process_downloaded_record(dict(data))
        with pytest.raises(Exception) as guarded:
            await storage._decode_record(dict(data))
        assert type(guarded.value) is type(direct.value)
        assert str(guarded.value) == str(direct.value)
        assert not _slot_taken()

    async def test_streamed_frame_takes_the_gate_skips_the_ceiling_and_fails_as_today(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(bs_mod._MAX_DECODED_BYTES_ENV, "1")
        obj = zstd.ZstdCompressor().compressobj()
        frame = obj.compress(msgspec.msgpack.encode(_record(5000))) + obj.flush()
        data = {"isCompressed": True, "record": base64.b64encode(frame).decode()}

        with pytest.raises(Exception, match="Decompression failed"):
            await _bs()._decode_record(data)

        slot = _decode_slot_for(asyncio.get_running_loop())
        await _until(lambda: slot._value == bs_mod.LARGE_DECODE_SLOTS)

    async def test_header_claiming_a_smaller_size_fails_instead_of_overrunning(self) -> None:
        """zstd sizes its output buffer from the header, so the gate's size can be trusted."""
        packed = msgspec.msgpack.encode(_record(100_000))
        frame = bytearray(zstd.ZstdCompressor().compress(packed))
        at = bytes(frame).find(struct.pack("<I", len(packed)))
        assert at > 0
        frame[at:at + 4] = struct.pack("<I", 100)
        data = {"isCompressed": True, "record": base64.b64encode(bytes(frame)).decode()}

        assert _compressed_content_size(data) == 100
        with pytest.raises(Exception, match="Decompression failed"):
            await _bs()._decode_record(data)


class TestCeiling:
    async def test_no_limit_by_default_even_for_a_record_claiming_3_gib(self) -> None:
        packed = msgspec.msgpack.encode(_record(100_000))
        frame = bytearray(zstd.ZstdCompressor().compress(packed))
        at = bytes(frame).find(struct.pack("<I", len(packed)))
        frame[at:at + 4] = struct.pack("<I", 3 * 1024**3)
        data = {"isCompressed": True, "record": base64.b64encode(bytes(frame)).decode()}
        assert _compressed_content_size(data) == 3 * 1024**3
        # Not refused: it reaches the decode, which then fails on the forged size.
        with pytest.raises(Exception, match="Decompression failed"):
            await _bs()._decode_record(data)

    async def test_over_the_ceiling_is_refused_before_any_decode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        record = _record(5000)
        monkeypatch.setenv(bs_mod._MAX_DECODED_BYTES_ENV, str(_msgpack_len(record) - 1))
        storage = _bs()
        with (
            patch.object(storage, "_decompress_bytes", side_effect=AssertionError("decoded")),
            patch.object(storage, "_decompressed_payload", side_effect=AssertionError("decoded")),
            pytest.raises(StoredRecordTooLargeError, match=str(_msgpack_len(record))),
        ):
            await storage._decode_record(_envelope(record))
        assert not _slot_taken()

    async def test_a_record_under_the_gate_is_still_held_to_the_ceiling(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The ceiling caps every record, not only the ones big enough to take the decode gate."""
        record = _record(100)
        assert _msgpack_len(record) < LARGE
        monkeypatch.setenv(bs_mod._MAX_DECODED_BYTES_ENV, str(_msgpack_len(record) - 1))
        storage = _bs()
        with (
            patch.object(storage, "_decompressed_payload", side_effect=AssertionError("decoded")),
            pytest.raises(StoredRecordTooLargeError, match=str(_msgpack_len(record))),
        ):
            await storage._decode_record(_envelope(record))

    async def test_a_record_under_the_gate_and_the_ceiling_decodes_inline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        record = _record(100)
        monkeypatch.setenv(bs_mod._MAX_DECODED_BYTES_ENV, str(_msgpack_len(record)))
        assert await _bs()._decode_record(_envelope(record)) == record
        assert not _slot_taken()

    async def test_exactly_the_ceiling_is_decoded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        record = _record(5000)
        monkeypatch.setenv(bs_mod._MAX_DECODED_BYTES_ENV, str(_msgpack_len(record)))
        assert await _bs()._decode_record(_envelope(record)) == record


class TestGate:
    async def test_large_decodes_share_one_dedicated_thread(self) -> None:
        """Default-executor threads each keep a malloc arena; one thread keeps one."""
        storage = _bs()
        names: set[str] = set()
        real = storage._decompressed_payload

        def spy(data: dict) -> bytes:
            names.add(threading.current_thread().name)
            return real(data)

        with patch.object(storage, "_decompressed_payload", side_effect=spy):
            for _ in range(5):
                await storage._decode_record(_envelope(_record(5000)))
        assert len(names) == 1
        assert next(iter(names)).startswith("record-decode")

    async def test_only_decompression_leaves_the_loop_thread(self) -> None:
        """msgpack holds the GIL anyway; built on the loop thread, the record uses the main malloc arena."""
        storage = _bs()
        where: dict[str, int] = {}
        real_payload, real_unpack = storage._decompressed_payload, storage._unpack_payload

        def payload(data: dict) -> bytes:
            where["decompress"] = threading.get_ident()
            return real_payload(data)

        def unpack(raw: bytes) -> dict:
            where["unpack"] = threading.get_ident()
            return real_unpack(raw)

        with patch.object(storage, "_decompressed_payload", side_effect=payload), patch.object(
            storage, "_unpack_payload", side_effect=unpack
        ):
            assert await storage._decode_record(_envelope(_record(5000))) == _record(5000)
        assert where["unpack"] == threading.get_ident()
        assert where["decompress"] != threading.get_ident()

    async def test_slot_is_released_when_unpacking_fails(self) -> None:
        storage = _bs()
        with patch.object(storage, "_decompressed_payload", return_value=b"\xc1 not msgpack"), pytest.raises(
            Exception, match="Decompression failed"
        ):
            await storage._decode_record(_envelope(_record(5000)))
        assert _decode_slot_for(asyncio.get_running_loop())._value == bs_mod.LARGE_DECODE_SLOTS
        assert await storage._decode_record(_envelope(_record(5000))) == _record(5000)

    async def test_large_record_round_trips_through_a_thread(self) -> None:
        record = _record(50_000)
        main_thread = threading.get_ident()
        storage = _bs()
        seen: list[int] = []
        real = storage._decompressed_payload

        def spy(data: dict) -> bytes:
            seen.append(threading.get_ident())
            return real(data)

        with patch.object(storage, "_decompressed_payload", side_effect=spy):
            assert await storage._decode_record(_envelope(record)) == record
        assert seen and seen[0] != main_thread

    async def test_one_large_decode_at_a_time(self) -> None:
        storage, fake = _bs(), _Blocking()
        envelope = _envelope(_record(5000))
        with patch.object(storage, "_decompressed_payload", side_effect=fake):
            first = asyncio.create_task(storage._decode_record(envelope))
            second = asyncio.create_task(storage._decode_record(envelope))
            await _until(fake.entered.is_set)
            await asyncio.sleep(0.05)
            assert fake.calls == 1
            fake.release.set()
            assert await asyncio.gather(first, second) == [fake.result, fake.result]
        assert fake.max_inside == 1

    async def test_cancelled_caller_holds_the_slot_until_its_thread_finishes(self) -> None:
        storage, fake = _bs(), _Blocking()
        envelope = _envelope(_record(5000))
        with patch.object(storage, "_decompressed_payload", side_effect=fake):
            first = asyncio.create_task(storage._decode_record(envelope))
            await _until(fake.entered.is_set)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first

            second = asyncio.create_task(storage._decode_record(envelope))
            await asyncio.sleep(0.1)
            assert fake.calls == 1, "second decode started while the first thread still ran"

            fake.release.set()
            assert await second == fake.result
        assert fake.max_inside == 1

    async def test_cancelled_caller_whose_thread_fails_leaves_no_unretrieved_error(self) -> None:
        loop = asyncio.get_running_loop()
        reported: list[dict] = []
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, ctx: reported.append(ctx))
        try:
            storage, fake = _bs(), _Blocking(error=ValueError("boom"))
            with patch.object(storage, "_decompressed_payload", side_effect=fake):
                task = asyncio.create_task(storage._decode_record(_envelope(_record(5000))))
                await _until(fake.entered.is_set)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                fake.release.set()
                slot = _decode_slot_for(loop)
                await _until(lambda: slot._value == bs_mod.LARGE_DECODE_SLOTS)
            del task
            gc.collect()
            await asyncio.sleep(0)
        finally:
            loop.set_exception_handler(previous)
        assert not [c for c in reported if "never retrieved" in str(c.get("message", ""))]

    async def test_a_reader_waits_its_turn_however_long_it_takes(self) -> None:
        """No time limit: under load a record is late, never dropped from the answer."""
        storage, fake = _bs(), _Blocking()
        envelope = _envelope(_record(5000))
        with patch.object(storage, "_decompressed_payload", side_effect=fake):
            holder = asyncio.create_task(storage._decode_record(envelope))
            await _until(fake.entered.is_set)
            waiter = asyncio.create_task(storage._decode_record(envelope))
            await asyncio.sleep(0.3)
            assert not waiter.done()
            fake.release.set()
            assert await asyncio.gather(holder, waiter) == [fake.result, fake.result]
        assert fake.calls == 2

    async def test_a_cancelled_waiter_leaves_the_queue(self) -> None:
        storage, fake = _bs(), _Blocking()
        envelope = _envelope(_record(5000))
        with patch.object(storage, "_decompressed_payload", side_effect=fake):
            holder = asyncio.create_task(storage._decode_record(envelope))
            await _until(fake.entered.is_set)
            waiter = asyncio.create_task(storage._decode_record(envelope))
            await asyncio.sleep(0.05)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            fake.release.set()
            await holder
        assert fake.calls == 1
        assert _decode_slot_for(asyncio.get_running_loop())._value == bs_mod.LARGE_DECODE_SLOTS

    async def test_a_failing_decode_releases_the_slot(self) -> None:
        storage = _bs()
        broken = _Blocking(error=ValueError("corrupt"))
        broken.release.set()
        envelope = _envelope(_record(5000))
        with patch.object(storage, "_decompressed_payload", side_effect=broken), pytest.raises(
            ValueError, match="corrupt"
        ):
            await storage._decode_record(envelope)
        assert await storage._decode_record(envelope) == _record(5000)

    async def test_small_records_are_not_held_up_by_a_large_one(self) -> None:
        storage, fake = _bs(), _Blocking()
        real = storage._decompressed_payload
        small = _envelope(_record(10))

        def route(data: dict) -> bytes:
            return fake(data) if data is not small else real(data)

        with patch.object(storage, "_decompressed_payload", side_effect=route):
            large = asyncio.create_task(storage._decode_record(_envelope(_record(5000))))
            await _until(fake.entered.is_set)
            assert await asyncio.wait_for(storage._decode_record(small), 1) == _record(10)
            fake.release.set()
            await large

    async def test_event_loop_keeps_running_during_a_large_decode(self) -> None:
        storage = _bs()

        def slow(data: dict) -> bytes:
            time.sleep(0.3)
            return msgspec.msgpack.encode({"ok": True})

        lags: list[float] = []

        async def ticker(stop: asyncio.Event) -> None:
            while not stop.is_set():
                t = time.perf_counter()
                await asyncio.sleep(0.01)
                lags.append(time.perf_counter() - t - 0.01)

        stop = asyncio.Event()
        tick = asyncio.create_task(ticker(stop))
        with patch.object(storage, "_decompressed_payload", side_effect=slow):
            await storage._decode_record(_envelope(_record(5000)))
        stop.set()
        await tick
        assert len(lags) > 10
        assert max(lags) < 0.15


class TestPerLoopSlots:
    def test_each_event_loop_gets_its_own_slot(self) -> None:
        """One semaphore shared by two loops would park the second loop's waiter forever."""
        storage, fake = _bs(), _Blocking()
        results: list[object] = []

        def first_loop() -> None:
            results.append(asyncio.run(storage._decode_record(_envelope(_record(5000)))))

        async def take_a_slot_on_another_loop() -> bool:
            slot = await asyncio.wait_for(bs_mod._acquire_decode_slot(), 2)
            slot.release()
            return True

        with patch.object(storage, "_decompressed_payload", side_effect=fake):
            holder = threading.Thread(target=first_loop)
            holder.start()
            assert fake.entered.wait(5)
            assert asyncio.run(take_a_slot_on_another_loop())
            fake.release.set()
            holder.join(10)
        assert results == [fake.result]

    def test_closed_loops_are_evicted(self) -> None:
        old = asyncio.new_event_loop()
        _decode_slot_for(old)
        old.close()
        fresh = asyncio.new_event_loop()
        try:
            _decode_slot_for(fresh)
            assert old not in bs_mod._decode_slots
            assert fresh in bs_mod._decode_slots
        finally:
            fresh.close()

    def test_same_loop_gets_the_same_slot(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            assert _decode_slot_for(loop) is _decode_slot_for(loop)
        finally:
            loop.close()


def _reader(
    storage: BlobStorage, *, cached_url: str | None = None, envelope: dict | None = None
) -> BlobStorage:
    storage._get_auth_and_config = AsyncMock(return_value=({}, "http://node", "s3"))
    storage._cached_signed_url = AsyncMock(return_value=cached_url)
    storage._store_signed_url = AsyncMock()
    storage._fetch_record_envelope = AsyncMock(return_value=envelope or {})
    return storage


_LOOKUP = {"record_doc_id": "doc-1", "fileSizeBytes": 10}


class TestGetRecordFromStorage:
    async def test_inline_envelope_goes_through_the_guard(self) -> None:
        storage = _reader(_bs(), envelope={"record": "x", "isCompressed": True})
        storage._decode_record = AsyncMock(return_value={"record_name": "r"})
        with patch.object(bs_mod, "get_shared_session", return_value=MagicMock()):
            assert await storage.get_record_from_storage("vr-1", "org", _LOOKUP) == {"record_name": "r"}
        storage._decode_record.assert_awaited_once()

    async def test_signed_url_download_goes_through_the_guard(self) -> None:
        record = _record(5000)
        body = msgspec.json.encode(_envelope(record))
        storage = _reader(_bs(), envelope={"signedUrl": "https://s3/x"})
        storage._download_with_range_requests = AsyncMock(return_value=body)
        with patch.object(bs_mod, "get_shared_session", return_value=MagicMock()), patch.object(
            storage, "_decode_record", wraps=storage._decode_record
        ) as spy:
            got = await storage.get_record_from_storage(
                "vr-1", "org", {"record_doc_id": "doc-1", "fileSizeBytes": None}
            )
        assert got == record
        spy.assert_awaited_once()
        assert _slot_taken()

    async def test_refused_decode_on_a_cached_url_is_not_downloaded_again(self) -> None:
        refusal = StoredRecordTooLargeError("too big")
        storage = _reader(_bs(), cached_url="https://s3/cached")
        storage._record_from_signed_url = AsyncMock(side_effect=refusal)
        with patch.object(bs_mod, "get_shared_session", return_value=MagicMock()), pytest.raises(
            type(refusal)
        ):
            await storage.get_record_from_storage("vr-1", "org", _LOOKUP)
        storage._record_from_signed_url.assert_awaited_once()
        storage._fetch_record_envelope.assert_not_awaited()
        storage.logger.warning.assert_called()
        storage.logger.exception.assert_not_called()

    async def test_other_cached_url_failures_still_re_sign(self) -> None:
        storage = _reader(_bs(), cached_url="https://s3/cached", envelope={"record": {"a": 1}})
        storage._record_from_signed_url = AsyncMock(side_effect=RuntimeError("expired"))
        with patch.object(bs_mod, "get_shared_session", return_value=MagicMock()):
            assert await storage.get_record_from_storage("vr-1", "org", _LOOKUP) == {"a": 1}
        storage._fetch_record_envelope.assert_awaited_once()

    async def test_over_the_ceiling_is_a_warning_not_a_traceback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(bs_mod._MAX_DECODED_BYTES_ENV, "600")
        storage = _reader(_bs(), envelope=_envelope(_record(5000)))
        with patch.object(bs_mod, "get_shared_session", return_value=MagicMock()), pytest.raises(
            StoredRecordTooLargeError
        ):
            await storage.get_record_from_storage("vr-big", "org", _LOOKUP)
        storage.logger.exception.assert_not_called()
        (args, _), = storage.logger.warning.call_args_list
        assert "vr-big" in args


class TestStoredSizeGatesTheDownload:
    """A record already known to be large downloads inside the slot, so queued readers hold nothing."""

    async def _hold_slot(self) -> asyncio.Semaphore:
        slot = _decode_slot_for(asyncio.get_running_loop())
        await slot.acquire()
        return slot

    async def test_large_stored_record_waits_for_the_slot_before_downloading(self) -> None:
        record = _record(5000)
        storage = _reader(_bs(), envelope=_envelope(record))
        slot = await self._hold_slot()
        with patch.object(bs_mod, "get_shared_session", return_value=MagicMock()):
            task = asyncio.create_task(
                storage.get_record_from_storage("vr-1", "org", {"record_doc_id": "doc-1", "fileSizeBytes": LARGE + 1})
            )
            await asyncio.sleep(0.05)
            storage._fetch_record_envelope.assert_not_awaited()
            slot.release()
            assert await task == record
        storage._fetch_record_envelope.assert_awaited_once()
        await _until(lambda: slot._value == bs_mod.LARGE_DECODE_SLOTS)

    @pytest.mark.parametrize("stored", [None, LARGE, 10])
    async def test_small_or_unknown_stored_size_downloads_without_waiting(self, stored: int | None) -> None:
        storage = _reader(_bs(), envelope={"record": {"a": 1}})
        slot = await self._hold_slot()
        try:
            with patch.object(bs_mod, "get_shared_session", return_value=MagicMock()):
                got = await asyncio.wait_for(
                    storage.get_record_from_storage("vr-1", "org", {"record_doc_id": "doc-1", "fileSizeBytes": stored}),
                    1,
                )
            assert got == {"a": 1}
        finally:
            slot.release()

    async def test_the_held_slot_is_used_for_the_decode_and_released_after(self) -> None:
        """Downloading inside the slot must not then queue for a second one (that would deadlock)."""
        record = _record(5000)
        storage = _reader(_bs(), envelope=_envelope(record))
        with patch.object(bs_mod, "get_shared_session", return_value=MagicMock()):
            got = await asyncio.wait_for(
                storage.get_record_from_storage("vr-1", "org", {"record_doc_id": "doc-1", "fileSizeBytes": LARGE + 1}),
                2,
            )
        assert got == record
        slot = _decode_slot_for(asyncio.get_running_loop())
        await _until(lambda: slot._value == bs_mod.LARGE_DECODE_SLOTS)

    @pytest.mark.parametrize(
        "envelope, error",
        [
            ({}, Exception),  # nothing to decode
            ({"isCompressed": True, "record": "!!not base64!!"}, Exception),  # inline decode fails
        ],
    )
    async def test_slot_is_released_when_the_fetch_fails_without_a_decode_thread(
        self, envelope: dict, error: type[Exception]
    ) -> None:
        storage = _reader(_bs(), envelope=envelope)
        with patch.object(bs_mod, "get_shared_session", return_value=MagicMock()), pytest.raises(error):
            await storage.get_record_from_storage("vr-1", "org", {"record_doc_id": "doc-1", "fileSizeBytes": LARGE + 1})
        assert _decode_slot_for(asyncio.get_running_loop())._value == bs_mod.LARGE_DECODE_SLOTS

    async def test_slot_is_released_when_the_ceiling_refuses_the_record(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(bs_mod._MAX_DECODED_BYTES_ENV, "600")
        storage = _reader(_bs(), envelope=_envelope(_record(5000)))
        with patch.object(bs_mod, "get_shared_session", return_value=MagicMock()), pytest.raises(
            StoredRecordTooLargeError
        ):
            await storage.get_record_from_storage("vr-1", "org", {"record_doc_id": "doc-1", "fileSizeBytes": LARGE + 1})
        assert _decode_slot_for(asyncio.get_running_loop())._value == bs_mod.LARGE_DECODE_SLOTS

    async def test_slot_is_released_when_the_caller_is_cancelled_mid_download(self) -> None:
        storage = _reader(_bs())
        started = asyncio.Event()

        async def slow_download(*_args: object) -> dict:
            started.set()
            await asyncio.sleep(10)
            return {}

        storage._fetch_record_envelope = AsyncMock(side_effect=slow_download)
        with patch.object(bs_mod, "get_shared_session", return_value=MagicMock()):
            task = asyncio.create_task(
                storage.get_record_from_storage("vr-1", "org", {"record_doc_id": "doc-1", "fileSizeBytes": LARGE + 1})
            )
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert _decode_slot_for(asyncio.get_running_loop())._value == bs_mod.LARGE_DECODE_SLOTS

    async def test_cancelled_mid_decode_keeps_the_slot_until_the_thread_finishes(self) -> None:
        storage, fake = _reader(_bs(), envelope=_envelope(_record(5000))), _Blocking()
        with patch.object(bs_mod, "get_shared_session", return_value=MagicMock()), patch.object(
            storage, "_decompressed_payload", side_effect=fake
        ):
            task = asyncio.create_task(
                storage.get_record_from_storage("vr-1", "org", {"record_doc_id": "doc-1", "fileSizeBytes": LARGE + 1})
            )
            await _until(fake.entered.is_set)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            slot = _decode_slot_for(asyncio.get_running_loop())
            assert slot._value == 0, "slot freed while the decode thread still runs"
            fake.release.set()
            await _until(lambda: slot._value == bs_mod.LARGE_DECODE_SLOTS)
