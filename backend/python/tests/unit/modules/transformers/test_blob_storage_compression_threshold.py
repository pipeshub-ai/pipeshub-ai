"""Only records above the size threshold are compressed.

Small records are stored as plain JSON so readers skip the base64 + zstd +
msgpack decode; the read path must accept both shapes unchanged.
"""

import json
from unittest.mock import MagicMock

import pytest

from app.modules.transformers import blob_storage as bs_mod
from app.modules.transformers.blob_storage import (
    COMPRESSION_THRESHOLD_BYTES_DEFAULT,
    BlobStorage,
    compression_threshold_bytes,
)


@pytest.fixture(autouse=True)
def _clear_compression_threshold_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(bs_mod._COMPRESSION_THRESHOLD_ENV, raising=False)


def _make_blob_storage() -> BlobStorage:
    return BlobStorage(logger=MagicMock(), config_service=MagicMock(), graph_provider=MagicMock())


def _record_of_json_size(target_bytes: int) -> dict:
    """A record whose json.dumps length is at least `target_bytes`."""
    record = {"id": "rec-1", "blob": "x" * target_bytes}
    assert len(json.dumps(record).encode("utf-8")) >= target_bytes
    return record


class TestThresholdConfig:
    def test_default_is_20mb(self) -> None:
        assert COMPRESSION_THRESHOLD_BYTES_DEFAULT == 20 * 1024 * 1024
        assert compression_threshold_bytes() == 20 * 1024 * 1024

    def test_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv(bs_mod._COMPRESSION_THRESHOLD_ENV, "1024")
        assert compression_threshold_bytes() == 1024

    def test_zero_env_compresses_everything(self, monkeypatch) -> None:
        monkeypatch.setenv(bs_mod._COMPRESSION_THRESHOLD_ENV, "0")
        assert compression_threshold_bytes() == 0

    def test_invalid_env_falls_back_to_default(self, monkeypatch) -> None:
        monkeypatch.setenv(bs_mod._COMPRESSION_THRESHOLD_ENV, "twenty-megs")
        assert compression_threshold_bytes() == COMPRESSION_THRESHOLD_BYTES_DEFAULT

    def test_negative_env_is_clamped(self, monkeypatch) -> None:
        monkeypatch.setenv(bs_mod._COMPRESSION_THRESHOLD_ENV, "-5")
        assert compression_threshold_bytes() == 0


def _body(blob: BlobStorage, record: dict) -> tuple[dict, bool]:
    """The envelope ``_upload_body`` writes, as a reader receives it."""
    body, is_compressed = blob._upload_body(record, "vr-1")
    return json.loads(body), is_compressed


class TestUploadBody:
    def test_small_record_is_not_compressed(self) -> None:
        envelope, is_compressed = _body(_make_blob_storage(), {"key": "value"})
        assert is_compressed is False
        assert envelope == {"isCompressed": False, "record": {"key": "value"}, "virtualRecordId": "vr-1"}

    def test_record_above_threshold_is_compressed(self, monkeypatch) -> None:
        monkeypatch.setenv(bs_mod._COMPRESSION_THRESHOLD_ENV, "1024")
        envelope, is_compressed = _body(_make_blob_storage(), _record_of_json_size(4096))
        assert is_compressed is True
        assert envelope["isCompressed"] is True
        assert isinstance(envelope["record"], str) and envelope["record"]

    def test_compression_disabled_stores_large_record_as_json(self, monkeypatch) -> None:
        # Plain JSON stays greppable by storage pattern match.
        monkeypatch.setenv("BLOB_STORAGE_COMPRESSION", "false")
        monkeypatch.setenv(bs_mod._COMPRESSION_THRESHOLD_ENV, "1024")
        record = _record_of_json_size(4096)
        envelope, is_compressed = _body(_make_blob_storage(), record)
        assert is_compressed is False
        assert envelope["record"] == record

    def test_boundary_is_exclusive(self, monkeypatch) -> None:
        """Exactly at the threshold stays uncompressed; one byte over compresses."""
        blob = _make_blob_storage()
        record = {"blob": "x" * 500}
        exact_size = len(json.dumps(record).encode("utf-8"))

        monkeypatch.setenv(bs_mod._COMPRESSION_THRESHOLD_ENV, str(exact_size))
        assert blob._upload_body(record, "vr-1")[1] is False

        monkeypatch.setenv(bs_mod._COMPRESSION_THRESHOLD_ENV, str(exact_size - 1))
        assert blob._upload_body(record, "vr-1")[1] is True

    def test_non_json_serializable_record_is_compressed(self, monkeypatch) -> None:
        """The uncompressed envelope is json.dumps'd, so it could not carry this."""
        monkeypatch.setenv(bs_mod._COMPRESSION_THRESHOLD_ENV, str(10 * 1024 * 1024))
        envelope, is_compressed = _body(
            _make_blob_storage(), {"when": {1, 2, 3}}  # a set: msgpack encodes it, json.dumps does not
        )
        assert is_compressed is True
        assert isinstance(envelope["record"], str)

    def test_compression_failure_falls_back_to_uncompressed(self, monkeypatch) -> None:
        monkeypatch.setenv(bs_mod._COMPRESSION_THRESHOLD_ENV, "10")
        blob = _make_blob_storage()
        blob._compress_record = MagicMock(side_effect=RuntimeError("zstd exploded"))
        record = _record_of_json_size(1024)

        envelope, is_compressed = _body(blob, record)

        assert is_compressed is False
        assert envelope["record"] == record
        blob.logger.warning.assert_called()


class TestRoundTripBothShapes:
    """`_process_downloaded_record` must read what the writer now produces."""

    def test_uncompressed_envelope_round_trips(self) -> None:
        blob = _make_blob_storage()
        record = {"id": "rec-1", "record_name": "small.txt", "blocks": [1, 2, 3]}

        envelope, _ = _body(blob, record)

        assert blob._process_downloaded_record(envelope) == record

    def test_compressed_envelope_round_trips(self, monkeypatch) -> None:
        monkeypatch.setenv(bs_mod._COMPRESSION_THRESHOLD_ENV, "16")
        blob = _make_blob_storage()
        record = {"id": "rec-1", "record_name": "big.txt", "body": "y" * 4096}

        envelope, is_compressed = _body(blob, record)
        assert is_compressed is True

        assert blob._process_downloaded_record(envelope) == record

    @pytest.mark.parametrize("threshold", ["0", "999999999"])
    def test_writer_and_reader_agree_at_any_threshold(self, monkeypatch, threshold) -> None:
        monkeypatch.setenv(bs_mod._COMPRESSION_THRESHOLD_ENV, threshold)
        blob = _make_blob_storage()
        record = {"id": "rec-1", "text": "hello world", "n": 42, "nested": {"a": [1, 2]}}

        envelope, _ = _body(blob, record)

        assert blob._process_downloaded_record(envelope) == record
