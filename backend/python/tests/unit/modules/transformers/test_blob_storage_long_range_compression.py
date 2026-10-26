"""Large records compress with a 128 MiB window and long-distance matching.

A deck repeats the same base64 image on every slide. At level 10's 4 MiB
window, repeats further apart than that were never matched, so a 112 MiB deck
record stored at 111 MiB and failed the local upload limit. These pin that
repeats ~10 MiB apart are found, that the frame still decodes with a default
decompressor, and that it carries the content size the read guard depends on.
"""

import base64
import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import msgspec
import pytest
import zstandard as zstd

from app.modules.transformers import blob_storage as bs_mod
from app.modules.transformers.blob_storage import (
    COMPRESSION_LEVEL,
    COMPRESSION_WINDOW_LOG,
    BlobStorage,
    _versioned_json_form,
)

MiB = 1024 * 1024


def _bs() -> BlobStorage:
    return BlobStorage(logger=MagicMock(), config_service=MagicMock(), graph_provider=MagicMock())


@pytest.fixture(scope="module")
def deck_like_record() -> dict:
    """>20 MiB: one ~1 MiB random image repeated with ~9 MiB between copies."""
    image = "data:image/png;base64," + base64.b64encode(os.urandom(MiB)).decode()
    gap = "x" * (9 * MiB)
    blocks = []
    for i in range(3):
        blocks.append({"type": "image", "index": 2 * i, "data": {"uri": image}})
        blocks.append({"type": "text", "index": 2 * i + 1, "data": gap})
    record = {"record_name": "deck.pptx", "block_containers": {"blocks": blocks}}
    assert len(msgspec.msgpack.encode(record)) > 20 * MiB
    return record


def _raw(encoded: str) -> bytes:
    return base64.b64decode(encoded)


class TestLongRangeCompression:
    def test_round_trips_through_the_envelope(self, deck_like_record: dict) -> None:
        storage = _bs()
        envelope = {"isCompressed": True, "record": storage._compress_record(deck_like_record)}
        assert storage._process_downloaded_record(envelope) == deck_like_record

    def test_finds_repeats_beyond_the_old_window(self, deck_like_record: dict) -> None:
        packed = msgspec.msgpack.encode(deck_like_record)
        before = len(zstd.ZstdCompressor(level=10).compress(packed))
        after = len(_raw(_bs()._compress_record(deck_like_record)))
        # Three copies of an incompressible 1 MiB image cost ~3 MiB before, ~1 MiB now.
        assert after < before / 2

    def test_frame_carries_the_content_size(self, deck_like_record: dict) -> None:
        packed = msgspec.msgpack.encode(deck_like_record)
        params = zstd.get_frame_parameters(_raw(_bs()._compress_record(deck_like_record)))
        assert params.content_size == len(packed)

    def test_window_fits_the_default_decoder_limit(self, deck_like_record: dict) -> None:
        raw = _raw(_bs()._compress_record(deck_like_record))
        assert zstd.get_frame_parameters(raw).window_size <= 1 << 27
        assert COMPRESSION_WINDOW_LOG <= 27
        assert zstd.ZstdDecompressor().decompress(raw) == msgspec.msgpack.encode(deck_like_record)

    def test_records_written_by_the_old_compressor_still_decode(self) -> None:
        record = {"record_name": "old", "blocks": [{"data": "text " * 1000}]}
        old = zstd.ZstdCompressor(level=10).compress(msgspec.msgpack.encode(record))
        envelope = {"isCompressed": True, "record": base64.b64encode(old).decode()}
        assert _bs()._process_downloaded_record(envelope) == record

    def test_concurrent_compression_from_threads(self) -> None:
        """Compressors are not thread-safe; one per call keeps threaded writers apart."""
        storage = _bs()
        records = [{"i": i, "data": os.urandom(256 * 1024).hex()} for i in range(8)]
        out: dict[int, str] = {}
        start = threading.Barrier(len(records))

        def work(i: int) -> None:
            start.wait()
            out[i] = storage._compress_record(records[i])

        threads = [threading.Thread(target=work, args=(i,)) for i in range(len(records))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for i, record in enumerate(records):
            assert storage._process_downloaded_record({"isCompressed": True, "record": out[i]}) == record


class TestRecordedLevel:
    def test_local_upload_metadata_names_the_level_used(self) -> None:
        form = _versioned_json_form(b"{}", "record_vr", "vr", "rec", compressed=True)
        fields = {opts["name"]: value for opts, _, value in form._fields}
        assert fields["customMetadata[0][value][level]"] == str(COMPRESSION_LEVEL)

    async def test_cloud_placeholder_metadata_names_the_level_used(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(bs_mod._COMPRESSION_THRESHOLD_ENV, "0")
        storage = _bs()
        storage._get_auth_and_config = AsyncMock(return_value=({}, "http://node", "s3"))
        storage._create_placeholder = AsyncMock(return_value={"_id": "doc-1"})
        storage._get_signed_url = AsyncMock(return_value={"signedUrl": "https://s3/put"})
        storage._upload_to_signed_url = AsyncMock()

        @asynccontextmanager
        async def session() -> AsyncIterator[MagicMock]:
            yield MagicMock()

        with patch.object(bs_mod, "_borrowed_session", session):
            await storage.save_record_to_storage("org", "rec", "vr", {"a": 1})

        placeholder = storage._create_placeholder.await_args.args[2]
        (compression,) = [m for m in placeholder["customMetadata"] if m["key"] == "compression"]
        assert compression["value"]["level"] == COMPRESSION_LEVEL
