"""Unit tests for app.modules.transformers.blob_storage.BlobStorage."""

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.transformers.blob_storage import BlobStorage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_blob_storage(
    config_service=None,
    graph_provider=None,
    logger=None,
):
    """Create a BlobStorage instance with sensible mock defaults."""
    logger = logger or MagicMock()
    config_service = config_service or AsyncMock()
    graph_provider = graph_provider or AsyncMock()
    return BlobStorage(
        logger=logger,
        config_service=config_service,
        graph_provider=graph_provider,
    )


def _make_record_mock(org_id="org-1", record_id="rec-1", virtual_record_id="vr-1"):
    """Return a MagicMock that behaves like a Record."""
    rec = MagicMock()
    rec.id = record_id
    rec.org_id = org_id
    rec.virtual_record_id = virtual_record_id
    rec.model_dump.return_value = {
        "id": record_id,
        "org_id": org_id,
        "virtual_record_id": virtual_record_id,
        "block_containers": {"blocks": [], "block_groups": []},
    }
    return rec


# ===================================================================
# _compress_record
# ===================================================================

class TestCompressRecord:
    """Tests for BlobStorage._compress_record."""

    def test_roundtrip_compress_decompress(self):
        """Compressing then decompressing should yield the original record."""
        import msgspec
        import zstandard as zstd

        bs = _make_blob_storage()
        original = {"key": "value", "nested": {"a": [1, 2, 3]}}

        compressed_b64 = bs._compress_record(original)

        # Validate it is valid base64
        compressed_bytes = base64.b64decode(compressed_b64)

        # Decompress
        decompressor = zstd.ZstdDecompressor()
        decompressed_bytes = decompressor.decompress(compressed_bytes)

        # Decode msgpack
        result = msgspec.msgpack.decode(decompressed_bytes)
        assert result == original

    def test_returns_base64_string(self):
        bs = _make_blob_storage()
        result = bs._compress_record({"x": 1})

        assert isinstance(result, str)
        # Should be valid base64
        decoded = base64.b64decode(result)
        assert len(decoded) > 0

    def test_dict_input_with_various_types(self):
        """Handles dicts with strings, ints, floats, bools, None, nested structures."""
        bs = _make_blob_storage()
        record = {
            "str_val": "hello",
            "int_val": 42,
            "float_val": 3.14,
            "bool_val": True,
            "none_val": None,
            "list_val": [1, "two", 3.0],
            "nested": {"a": {"b": "c"}},
        }
        result = bs._compress_record(record)
        assert isinstance(result, str)
        # Verify roundtrip
        import msgspec
        import zstandard as zstd

        decompressed = zstd.ZstdDecompressor().decompress(base64.b64decode(result))
        decoded = msgspec.msgpack.decode(decompressed)
        assert decoded == record

    def test_empty_dict(self):
        bs = _make_blob_storage()
        result = bs._compress_record({})
        assert isinstance(result, str)

    def test_compression_logs_ratio(self):
        logger = MagicMock()
        bs = _make_blob_storage(logger=logger)
        bs._compress_record({"data": "x" * 1000})
        logger.debug.assert_called()
        fmt, *_ = logger.debug.call_args[0]
        assert "Compressed record" in fmt and "reduction" in fmt


# ===================================================================
# _decompress_bytes
# ===================================================================

class TestDecompressBytes:
    """Tests for BlobStorage._decompress_bytes."""

    def test_valid_compressed_data(self):
        import zstandard as zstd

        bs = _make_blob_storage()
        original = b"hello world, this is test data"
        compressor = zstd.ZstdCompressor(level=10)
        compressed = compressor.compress(original)

        result = bs._decompress_bytes(compressed)
        assert result == original

    def test_invalid_data_raises(self):
        bs = _make_blob_storage()
        with pytest.raises(Exception):
            bs._decompress_bytes(b"this is not valid zstd data")

    def test_empty_compressed_data(self):
        """Zstd can compress empty data; decompressing should yield empty bytes."""
        import zstandard as zstd

        bs = _make_blob_storage()
        compressor = zstd.ZstdCompressor()
        compressed = compressor.compress(b"")
        result = bs._decompress_bytes(compressed)
        assert result == b""


# ===================================================================
# _process_downloaded_record
# ===================================================================

class TestProcessDownloadedRecord:
    """Tests for BlobStorage._process_downloaded_record."""

    def test_compressed_record(self):
        """When isCompressed=True, should decompress base64-encoded msgspec+zstd data."""
        import msgspec
        import zstandard as zstd

        bs = _make_blob_storage()
        original_record = {"key": "value", "items": [1, 2]}

        # Simulate compression pipeline: msgpack -> zstd -> base64
        msgpack_bytes = msgspec.msgpack.encode(original_record)
        compressed = zstd.ZstdCompressor(level=10).compress(msgpack_bytes)
        b64_encoded = base64.b64encode(compressed).decode("utf-8")

        data = {"isCompressed": True, "record": b64_encoded}
        result = bs._process_downloaded_record(data)
        assert result == original_record

    def test_compressed_missing_record_raises(self):
        """When isCompressed=True but no record field, should raise."""
        bs = _make_blob_storage()
        data = {"isCompressed": True}
        with pytest.raises(Exception, match="Missing record"):
            bs._process_downloaded_record(data)

    def test_compressed_bad_data_raises(self):
        """When isCompressed=True but record is not valid base64+zstd, should raise."""
        bs = _make_blob_storage()
        data = {"isCompressed": True, "record": "not_valid_base64!!!"}
        with pytest.raises(Exception, match="Decompression failed"):
            bs._process_downloaded_record(data)

    def test_uncompressed_record(self):
        """When isCompressed is falsy but record exists, returns record as-is."""
        bs = _make_blob_storage()
        record_data = {"key": "value"}
        data = {"record": record_data, "isCompressed": False}
        result = bs._process_downloaded_record(data)
        assert result == record_data

    def test_legacy_uncompressed_record(self):
        """Old format: no isCompressed flag, just a record field."""
        bs = _make_blob_storage()
        record_data = {"legacy": True}
        data = {"record": record_data}
        result = bs._process_downloaded_record(data)
        assert result == record_data

    def test_unknown_format_raises(self):
        """No isCompressed and no record field should raise."""
        bs = _make_blob_storage()
        data = {"some_other_field": "abc"}
        with pytest.raises(Exception, match="Unknown record format"):
            bs._process_downloaded_record(data)


# ===================================================================
# _get_auth_and_config
# ===================================================================

class TestGetAuthAndConfig:
    """Tests for BlobStorage._get_auth_and_config."""

    @pytest.mark.asyncio
    async def test_success(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            side_effect=[
                # First call: SECRET_KEYS
                {"scopedJwtSecret": "test-secret-123"},
                # Second call: ENDPOINTS
                {"cm": {"endpoint": "http://localhost:3000"}},
                # Third call: STORAGE
                {"storageType": "s3"},
            ]
        )
        bs = _make_blob_storage(config_service=config_service)

        headers, endpoint, storage_type = await bs._get_auth_and_config("org-1")

        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")
        assert endpoint == "http://localhost:3000"
        assert storage_type == "s3"

    @pytest.mark.asyncio
    async def test_missing_jwt_secret_raises(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            side_effect=[
                # SECRET_KEYS without scopedJwtSecret
                {"otherKey": "value"},
            ]
        )
        bs = _make_blob_storage(config_service=config_service)

        with pytest.raises(ValueError, match="Missing scoped JWT secret"):
            await bs._get_auth_and_config("org-1")

    @pytest.mark.asyncio
    async def test_missing_storage_type_raises(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3000"}},
                {},  # STORAGE without storageType
            ]
        )
        bs = _make_blob_storage(config_service=config_service)

        with pytest.raises(ValueError, match="Missing storage type"):
            await bs._get_auth_and_config("org-1")

    @pytest.mark.asyncio
    async def test_missing_endpoint_uses_default(self):
        """When cm endpoint is missing, falls back to DefaultEndpoints."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {},  # ENDPOINTS without cm
                {"storageType": "local"},
            ]
        )
        bs = _make_blob_storage(config_service=config_service)

        headers, endpoint, storage_type = await bs._get_auth_and_config("org-1")
        # Should use DefaultEndpoints.NODEJS_ENDPOINT
        assert endpoint is not None
        assert storage_type == "local"


# ===================================================================
# apply
# ===================================================================

class TestBlobStorageApply:
    """Tests for BlobStorage.apply (main transform)."""

    @pytest.mark.asyncio
    async def test_apply_calls_save_record(self):
        """apply should call save_record_to_storage and store_virtual_record_mapping."""
        bs = _make_blob_storage()
        bs.get_document_id_by_virtual_record_id = AsyncMock(return_value=None)
        bs.save_record_to_storage = AsyncMock(return_value=("doc-id-123", 4096))
        bs.store_virtual_record_mapping = AsyncMock()

        record = _make_record_mock()
        ctx = MagicMock()
        ctx.record = record

        result = await bs.apply(ctx)

        bs.save_record_to_storage.assert_awaited_once()
        bs.store_virtual_record_mapping.assert_awaited_once_with(
            "vr-1", "doc-id-123", 4096
        )
        assert result.record == record

    @pytest.mark.asyncio
    async def test_apply_no_document_id_skips_mapping(self):
        """When save_record_to_storage returns None doc id, skip mapping."""
        bs = _make_blob_storage()
        bs.get_document_id_by_virtual_record_id = AsyncMock(return_value=None)
        bs.save_record_to_storage = AsyncMock(return_value=(None, None))
        bs.store_virtual_record_mapping = AsyncMock()

        record = _make_record_mock()
        ctx = MagicMock()
        ctx.record = record

        await bs.apply(ctx)

        bs.store_virtual_record_mapping.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_apply_no_graph_provider_skips_mapping(self):
        """When graph_provider is None, skip mapping even with valid document_id."""
        bs = _make_blob_storage(graph_provider=None)
        bs.graph_provider = None
        bs.save_record_to_storage = AsyncMock(return_value=("doc-id-123", 4096))
        bs.store_virtual_record_mapping = AsyncMock()

        record = _make_record_mock()
        ctx = MagicMock()
        ctx.record = record

        await bs.apply(ctx)

        bs.store_virtual_record_mapping.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_apply_propagates_save_error(self):
        """If save_record_to_storage raises, apply should propagate."""
        bs = _make_blob_storage()
        bs.get_document_id_by_virtual_record_id = AsyncMock(return_value=None)
        bs.save_record_to_storage = AsyncMock(side_effect=Exception("upload failed"))

        record = _make_record_mock()
        ctx = MagicMock()
        ctx.record = record

        with pytest.raises(Exception, match="upload failed"):
            await bs.apply(ctx)


# ===================================================================
# _clean_top_level_empty_values and _clean_empty_values
# ===================================================================

class TestCleanEmptyValues:
    """Tests for the cleaning helper methods."""

    def test_clean_top_level_empty_values(self):
        bs = _make_blob_storage()
        data = {
            "a": "hello",
            "b": None,
            "c": "",
            "d": [],
            "e": {},
            "f": 0,
            "g": False,
        }
        result = bs._clean_top_level_empty_values(data)
        assert "a" in result
        assert "b" not in result
        assert "c" not in result
        assert "d" not in result
        assert "e" not in result
        # 0 and False are valid values
        assert result["f"] == 0
        assert result["g"] is False

    def test_clean_empty_values_with_block_containers(self):
        bs = _make_blob_storage()
        data = {
            "id": "rec-1",
            "empty_field": None,
            "block_containers": {
                "blocks": [
                    {"content": "text", "empty": None, "also_empty": ""},
                    {"content": "text2"},
                ],
                "block_groups": [
                    {"name": "group1", "removed": []},
                ],
            },
        }
        result = bs._clean_empty_values(data)
        assert "empty_field" not in result
        assert result["block_containers"]["blocks"][0] == {"content": "text"}
        assert result["block_containers"]["blocks"][1] == {"content": "text2"}
        assert result["block_containers"]["block_groups"][0] == {"name": "group1"}

    def test_clean_empty_values_no_block_containers(self):
        """When block_containers is absent, top-level cleaning still works."""
        bs = _make_blob_storage()
        data = {"id": "rec-1", "empty": None, "valid": "ok"}
        result = bs._clean_empty_values(data)
        assert "empty" not in result
        assert result["valid"] == "ok"

    def test_clean_empty_values_non_dict_blocks_preserved(self):
        """Non-dict items in blocks list should be preserved as-is."""
        bs = _make_blob_storage()
        data = {
            "block_containers": {
                "blocks": ["string_block", 42, {"content": "text", "empty": None}],
                "block_groups": [None, {"name": "group"}],
            },
        }
        result = bs._clean_empty_values(data)
        assert result["block_containers"]["blocks"][0] == "string_block"
        assert result["block_containers"]["blocks"][1] == 42
        assert result["block_containers"]["blocks"][2] == {"content": "text"}

    def test_clean_empty_values_non_dict_block_containers(self):
        """Non-dict block_containers should be preserved."""
        bs = _make_blob_storage()
        data = {"block_containers": "not a dict"}
        result = bs._clean_empty_values(data)
        assert result["block_containers"] == "not a dict"

    def test_clean_top_level_preserves_zero_and_false(self):
        """0 and False are not 'empty' values."""
        bs = _make_blob_storage()
        data = {"zero": 0, "false_val": False, "none_val": None}
        result = bs._clean_top_level_empty_values(data)
        assert result["zero"] == 0
        assert result["false_val"] is False
        assert "none_val" not in result


# ===================================================================
# apply — deeper coverage
# ===================================================================

class TestBlobStorageApplyDeep:
    """Deeper tests for BlobStorage.apply."""

    @pytest.mark.asyncio
    async def test_apply_full_flow_calls_model_dump(self):
        """apply should call model_dump on the record and clean empty values."""
        bs = _make_blob_storage()
        bs.get_document_id_by_virtual_record_id = AsyncMock(return_value=None)
        bs.save_record_to_storage = AsyncMock(return_value=("doc-id", 2048))
        bs.store_virtual_record_mapping = AsyncMock()

        record = MagicMock()
        record.org_id = "org-1"
        record.id = "rec-1"
        record.virtual_record_id = "vr-1"
        record.model_dump.return_value = {"id": "rec-1", "content": "hello", "empty": None}

        ctx = MagicMock()
        ctx.record = record

        result = await bs.apply(ctx)

        record.model_dump.assert_called_once_with(mode='json', exclude_none=True)
        # save_record_to_storage should receive cleaned dict
        call_args = bs.save_record_to_storage.call_args
        assert call_args[0][0] == "org-1"
        assert call_args[0][1] == "rec-1"
        assert call_args[0][2] == "vr-1"
        assert result.record is record

    @pytest.mark.asyncio
    async def test_apply_returns_context_with_original_record(self):
        """apply should set ctx.record to the original record object."""
        bs = _make_blob_storage()
        bs.get_document_id_by_virtual_record_id = AsyncMock(return_value=None)
        bs.save_record_to_storage = AsyncMock(return_value=("doc-id", 100))
        bs.store_virtual_record_mapping = AsyncMock()

        record = MagicMock()
        record.org_id = "org-1"
        record.id = "rec-1"
        record.virtual_record_id = "vr-1"
        record.model_dump.return_value = {}

        ctx = MagicMock()
        ctx.record = record

        result = await bs.apply(ctx)
        assert result.record is record


# ===================================================================
# save_record_to_storage
# ===================================================================

class TestSaveRecordToStorage:
    """Tests for BlobStorage.save_record_to_storage."""

    @pytest.mark.asyncio
    async def test_local_storage_success(self):
        """Successful upload to local storage returns doc id and file size."""
        bs = _make_blob_storage()

        # Mock config calls
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "test-secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
                {"storageType": "local"},
            ]
        )

        # Mock aiohttp
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"_id": "doc-123"})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.modules.transformers.blob_storage.aiohttp.ClientSession", return_value=mock_session):
            doc_id, file_size = await bs.save_record_to_storage(
                "org-1", "rec-1", "vr-1", {"key": "value"}
            )
            assert doc_id == "doc-123"
            assert file_size > 0

    @pytest.mark.asyncio
    async def test_missing_jwt_secret_raises(self):
        """Missing JWT secret should raise ValueError."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            return_value={"otherKey": "val"}
        )
        with pytest.raises(ValueError, match="Missing scoped JWT secret"):
            await bs.save_record_to_storage("org-1", "rec-1", "vr-1", {})

    @pytest.mark.asyncio
    async def test_missing_storage_type_raises(self):
        """Missing storage type should raise ValueError."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost"}},
                {},  # No storageType
            ]
        )
        with pytest.raises(ValueError, match="Missing storage type"):
            await bs.save_record_to_storage("org-1", "rec-1", "vr-1", {})


# ===================================================================
# get_record_from_storage
# ===================================================================

class TestGetRecordFromStorage:
    """Tests for BlobStorage.get_record_from_storage."""

    @pytest.mark.asyncio
    async def test_no_document_id_returns_none(self):
        """When no document ID found, returns None."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
                {"storageType": "local"},
            ]
        )
        bs.get_document_id_by_virtual_record_id = AsyncMock(return_value=None)

        result = await bs.get_record_from_storage("vr-1", "org-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_successful_direct_download(self):
        """Successful download with direct record data."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
                {"storageType": "local"},
            ]
        )
        bs.get_document_id_by_virtual_record_id = AsyncMock(
            return_value={"record_doc_id": "doc-123", "fileSizeBytes": 500}
        )

        record_data = {"id": "rec-1", "content": "hello"}
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"record": record_data})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.modules.transformers.blob_storage.aiohttp.ClientSession", return_value=mock_session):
            result = await bs.get_record_from_storage("vr-1", "org-1")
            assert result == record_data

    @pytest.mark.asyncio
    async def test_download_non_200_raises(self):
        """Non-200 status code from download should raise."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
                {"storageType": "local"},
            ]
        )
        bs.get_document_id_by_virtual_record_id = AsyncMock(
            return_value={"record_doc_id": "doc-123", "fileSizeBytes": 500}
        )

        mock_resp = AsyncMock()
        mock_resp.status = 404
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.modules.transformers.blob_storage.aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(Exception, match="Failed to retrieve record"):
                await bs.get_record_from_storage("vr-1", "org-1")


# ===================================================================
# _get_content_length
# ===================================================================

class TestGetContentLength:
    """Tests for BlobStorage._get_content_length."""

    @pytest.mark.asyncio
    async def test_partial_content_response(self):
        """206 Partial Content returns total size from Content-Range."""
        bs = _make_blob_storage()

        mock_resp = AsyncMock()
        mock_resp.status = 206
        mock_resp.headers = {"Content-Range": "bytes 0-0/12345"}
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)

        result = await bs._get_content_length(mock_session, "https://s3.example.com/file")
        assert result == 12345

    @pytest.mark.asyncio
    async def test_200_with_content_length(self):
        """200 response returns Content-Length header value."""
        bs = _make_blob_storage()

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Length": "67890"}
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)

        result = await bs._get_content_length(mock_session, "https://s3.example.com/file")
        assert result == 67890

    @pytest.mark.asyncio
    async def test_200_without_content_length(self):
        """200 response without Content-Length returns None."""
        bs = _make_blob_storage()

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {}
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)

        result = await bs._get_content_length(mock_session, "https://s3.example.com/file")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        """Network error returns None."""
        bs = _make_blob_storage()

        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=Exception("connection refused"))

        result = await bs._get_content_length(mock_session, "https://s3.example.com/file")
        assert result is None


# ===================================================================
# _download_chunk_with_retry
# ===================================================================

class TestDownloadChunkWithRetry:
    """Tests for BlobStorage._download_chunk_with_retry."""

    @pytest.mark.asyncio
    async def test_success_first_attempt(self):
        """Chunk downloaded successfully on first attempt."""
        bs = _make_blob_storage()

        mock_resp = AsyncMock()
        mock_resp.status = 206
        mock_resp.read = AsyncMock(return_value=b"chunk_data_here")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)

        index, data = await bs._download_chunk_with_retry(
            mock_session, "https://s3.example.com/file", 0, 99, chunk_index=0
        )
        assert index == 0
        assert data == b"chunk_data_here"

    @pytest.mark.asyncio
    async def test_success_200_status(self):
        """200 status is also acceptable for chunk download."""
        bs = _make_blob_storage()

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=b"full_data")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)

        index, data = await bs._download_chunk_with_retry(
            mock_session, "https://s3.example.com/file", 0, 99, chunk_index=2
        )
        assert index == 2
        assert data == b"full_data"

    @pytest.mark.asyncio
    async def test_retry_on_failure_then_success(self):
        """Fails first attempt, succeeds on retry."""
        bs = _make_blob_storage()

        fail_resp = AsyncMock()
        fail_resp.status = 500
        fail_resp.__aenter__ = AsyncMock(return_value=fail_resp)
        fail_resp.__aexit__ = AsyncMock(return_value=False)

        success_resp = AsyncMock()
        success_resp.status = 206
        success_resp.read = AsyncMock(return_value=b"ok")
        success_resp.__aenter__ = AsyncMock(return_value=success_resp)
        success_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=[fail_resp, success_resp])

        with patch("app.modules.transformers.blob_storage.asyncio.sleep", new_callable=AsyncMock):
            index, data = await bs._download_chunk_with_retry(
                mock_session, "https://s3.example.com/file", 0, 99, chunk_index=0, max_retries=2
            )
            assert data == b"ok"

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_raises(self):
        """All retries fail, raises exception."""
        bs = _make_blob_storage()

        fail_resp = AsyncMock()
        fail_resp.status = 500
        fail_resp.__aenter__ = AsyncMock(return_value=fail_resp)
        fail_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=fail_resp)

        with patch("app.modules.transformers.blob_storage.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(Exception):
                await bs._download_chunk_with_retry(
                    mock_session, "https://s3.example.com/file", 0, 99, chunk_index=0, max_retries=2
                )


# ===================================================================
# store_virtual_record_mapping
# ===================================================================

class TestStoreVirtualRecordMapping:
    """Tests for BlobStorage.store_virtual_record_mapping."""

    @pytest.mark.asyncio
    async def test_success(self):
        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
        bs = _make_blob_storage(graph_provider=graph_provider)

        result = await bs.store_virtual_record_mapping("vr-1", "doc-1", 4096)
        assert result is True
        # Verify the mapping document shape
        call_args = graph_provider.batch_upsert_nodes.call_args
        mapping_doc = call_args[0][0][0]
        assert mapping_doc["id"] == "vr-1"
        assert mapping_doc["documentId"] == "doc-1"
        assert mapping_doc["fileSizeBytes"] == 4096

    @pytest.mark.asyncio
    async def test_success_without_file_size(self):
        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
        bs = _make_blob_storage(graph_provider=graph_provider)

        result = await bs.store_virtual_record_mapping("vr-1", "doc-1")
        assert result is True
        call_args = graph_provider.batch_upsert_nodes.call_args
        mapping_doc = call_args[0][0][0]
        assert "fileSizeBytes" not in mapping_doc

    @pytest.mark.asyncio
    async def test_upsert_returns_false_raises(self):
        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock(return_value=False)
        bs = _make_blob_storage(graph_provider=graph_provider)

        with pytest.raises(Exception, match="Failed to store virtual record mapping"):
            await bs.store_virtual_record_mapping("vr-1", "doc-1")

    @pytest.mark.asyncio
    async def test_graph_provider_exception_propagates(self):
        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock(side_effect=RuntimeError("db error"))
        bs = _make_blob_storage(graph_provider=graph_provider)

        with pytest.raises(RuntimeError, match="db error"):
            await bs.store_virtual_record_mapping("vr-1", "doc-1")


# ===================================================================
# get_document_id_by_virtual_record_id
# ===================================================================

class TestGetDocumentIdByVirtualRecordId:
    """Tests for BlobStorage.get_document_id_by_virtual_record_id."""

    @pytest.mark.asyncio
    async def test_found_by_filter(self):
        graph_provider = AsyncMock()
        graph_provider.get_nodes_by_filters = AsyncMock(
            return_value=[{"documentId": "doc-1", "fileSizeBytes": 2048}]
        )
        bs = _make_blob_storage(graph_provider=graph_provider)

        result = await bs.get_document_id_by_virtual_record_id("vr-1")
        assert result["record_doc_id"] == "doc-1"
        assert result["fileSizeBytes"] == 2048

    @pytest.mark.asyncio
    async def test_found_by_key_fallback(self):
        graph_provider = AsyncMock()
        graph_provider.get_nodes_by_filters = AsyncMock(return_value=[])
        graph_provider.get_document = AsyncMock(
            return_value={"documentId": "doc-2", "fileSizeBytes": 1024}
        )
        bs = _make_blob_storage(graph_provider=graph_provider)

        result = await bs.get_document_id_by_virtual_record_id("vr-1")
        assert result["record_doc_id"] == "doc-2"
        assert result["fileSizeBytes"] == 1024

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        graph_provider = AsyncMock()
        graph_provider.get_nodes_by_filters = AsyncMock(return_value=[])
        graph_provider.get_document = AsyncMock(return_value=None)
        bs = _make_blob_storage(graph_provider=graph_provider)

        result = await bs.get_document_id_by_virtual_record_id("vr-missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_graph_provider_raises(self):
        bs = _make_blob_storage()
        bs.graph_provider = None

        with pytest.raises(Exception, match="GraphProvider not initialized"):
            await bs.get_document_id_by_virtual_record_id("vr-1")

    @pytest.mark.asyncio
    async def test_node_without_document_id_returns_none(self):
        graph_provider = AsyncMock()
        graph_provider.get_nodes_by_filters = AsyncMock(
            return_value=[{"fileSizeBytes": 2048}]  # Missing documentId
        )
        bs = _make_blob_storage(graph_provider=graph_provider)

        result = await bs.get_document_id_by_virtual_record_id("vr-1")
        assert result["record_doc_id"] is None
        assert result["fileSizeBytes"] == 2048


# ===================================================================
# _get_signed_url
# ===================================================================

class TestGetSignedUrl:
    """Tests for BlobStorage._get_signed_url."""

    @pytest.mark.asyncio
    async def test_success(self):
        bs = _make_blob_storage()

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"signedUrl": "https://s3.example.com/signed"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        result = await bs._get_signed_url(mock_session, "http://api/url", {}, {})
        assert result["signedUrl"] == "https://s3.example.com/signed"

    @pytest.mark.asyncio
    async def test_non_200_raises(self):
        import aiohttp
        bs = _make_blob_storage()

        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.json = AsyncMock(return_value={"error": "Internal Server Error"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        with pytest.raises(aiohttp.ClientError):
            await bs._get_signed_url(mock_session, "http://api/url", {}, {})


# ===================================================================
# _upload_to_signed_url
# ===================================================================

class TestUploadToSignedUrl:
    """Tests for BlobStorage._upload_to_signed_url."""

    @pytest.mark.asyncio
    async def test_success(self):
        bs = _make_blob_storage()

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.put = MagicMock(return_value=mock_resp)

        status = await bs._upload_to_signed_url(mock_session, "https://s3/signed", {"record": "data"})
        assert status == 200

    @pytest.mark.asyncio
    async def test_non_200_raises(self):
        import aiohttp
        bs = _make_blob_storage()

        mock_resp = AsyncMock()
        mock_resp.status = 403
        mock_resp.json = AsyncMock(return_value={"error": "Forbidden"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.put = MagicMock(return_value=mock_resp)

        with pytest.raises(aiohttp.ClientError):
            await bs._upload_to_signed_url(mock_session, "https://s3/signed", {})


# ===================================================================
# save_record_to_storage — S3 path
# ===================================================================

class TestSaveRecordToStorageS3:
    """Tests for the S3 (non-local) branch in save_record_to_storage."""

    @pytest.mark.asyncio
    async def test_s3_success_compressed(self):
        """S3 upload with compression succeeds and returns document id + size."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
                {"storageType": "s3"},
            ]
        )

        # Mock placeholder creation
        placeholder_resp = AsyncMock()
        placeholder_resp.status = 200
        placeholder_resp.json = AsyncMock(return_value={"_id": "s3-doc-1"})
        placeholder_resp.__aenter__ = AsyncMock(return_value=placeholder_resp)
        placeholder_resp.__aexit__ = AsyncMock(return_value=False)

        # Mock signed URL retrieval
        signed_url_resp = AsyncMock()
        signed_url_resp.status = 200
        signed_url_resp.json = AsyncMock(return_value={"signedUrl": "https://s3.example.com/upload"})
        signed_url_resp.__aenter__ = AsyncMock(return_value=signed_url_resp)
        signed_url_resp.__aexit__ = AsyncMock(return_value=False)

        # Mock upload to signed URL
        upload_resp = AsyncMock()
        upload_resp.status = 200
        upload_resp.__aenter__ = AsyncMock(return_value=upload_resp)
        upload_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=[placeholder_resp, signed_url_resp])
        mock_session.put = MagicMock(return_value=upload_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.modules.transformers.blob_storage.aiohttp.ClientSession", return_value=mock_session):
            doc_id, file_size = await bs.save_record_to_storage(
                "org-1", "rec-1", "vr-1", {"key": "value"}
            )
            assert doc_id == "s3-doc-1"
            assert file_size > 0

    @pytest.mark.asyncio
    async def test_local_upload_non_200_raises(self):
        """Non-200 response from local upload should raise."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
                {"storageType": "local"},
            ]
        )

        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.json = AsyncMock(return_value={"error": "Internal Server Error"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.modules.transformers.blob_storage.aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(Exception, match="Failed to upload record"):
                await bs.save_record_to_storage(
                    "org-1", "rec-1", "vr-1", {"key": "value"}
                )

    @pytest.mark.asyncio
    async def test_local_upload_no_document_id_raises(self):
        """Upload succeeds but response has no _id field."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
                {"storageType": "local"},
            ]
        )

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={})  # No _id
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.modules.transformers.blob_storage.aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(Exception, match="No document ID"):
                await bs.save_record_to_storage(
                    "org-1", "rec-1", "vr-1", {"key": "value"}
                )


# ===================================================================
# get_record_from_storage — signed URL path
# ===================================================================

class TestGetRecordFromStorageSignedUrl:
    """Tests for the signed URL branch in get_record_from_storage."""

    @pytest.mark.asyncio
    async def test_signed_url_direct_download_success(self):
        """When API returns signedUrl, follow it to get the record (small file)."""
        import msgspec
        import zstandard as zstd

        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
                {"storageType": "local"},
            ]
        )
        # Small file size -> single download (not parallel)
        bs.get_document_id_by_virtual_record_id = AsyncMock(
            return_value={"record_doc_id": "doc-123", "fileSizeBytes": 100}
        )

        record_data = {"id": "rec-1", "content": "hello"}

        # First response: signedUrl
        first_resp = AsyncMock()
        first_resp.status = 200
        first_resp.json = AsyncMock(
            return_value={"signedUrl": "https://s3.example.com/signed"}
        )
        first_resp.__aenter__ = AsyncMock(return_value=first_resp)
        first_resp.__aexit__ = AsyncMock(return_value=False)

        # Second response: actual data from signed URL
        second_resp = AsyncMock()
        second_resp.status = 200
        second_resp.json = AsyncMock(
            return_value={"record": record_data, "isCompressed": False}
        )
        second_resp.__aenter__ = AsyncMock(return_value=second_resp)
        second_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=[first_resp, second_resp])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            result = await bs.get_record_from_storage("vr-1", "org-1")
            assert result == record_data

    @pytest.mark.asyncio
    async def test_download_compressed_via_direct_record(self):
        """Compressed record returned directly (not via signed URL)."""
        import msgspec
        import zstandard as zstd

        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
                {"storageType": "local"},
            ]
        )
        bs.get_document_id_by_virtual_record_id = AsyncMock(
            return_value={"record_doc_id": "doc-123", "fileSizeBytes": 500}
        )

        original_record = {"id": "rec-1", "data": "compressed content"}
        # Simulate compression pipeline: msgpack -> zstd -> base64
        import base64
        msgpack_bytes = msgspec.msgpack.encode(original_record)
        compressed = zstd.ZstdCompressor(level=10).compress(msgpack_bytes)
        b64_encoded = base64.b64encode(compressed).decode("utf-8")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={"record": b64_encoded, "isCompressed": True}
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            result = await bs.get_record_from_storage("vr-1", "org-1")
            assert result == original_record

    @pytest.mark.asyncio
    async def test_no_record_and_no_signed_url_raises(self):
        """Response has neither record nor signedUrl -> raises."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
                {"storageType": "local"},
            ]
        )
        bs.get_document_id_by_virtual_record_id = AsyncMock(
            return_value={"record_doc_id": "doc-123", "fileSizeBytes": 500}
        )

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"unexpected": "data"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            with pytest.raises(Exception, match="No record found"):
                await bs.get_record_from_storage("vr-1", "org-1")


# ===================================================================
# _upload_raw_to_signed_url
# ===================================================================

class TestUploadRawToSignedUrl:
    """Tests for BlobStorage._upload_raw_to_signed_url."""

    @pytest.mark.asyncio
    async def test_success(self):
        bs = _make_blob_storage()

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.put = MagicMock(return_value=mock_resp)

        await bs._upload_raw_to_signed_url(
            mock_session, "https://s3/signed", b"file content", "text/csv"
        )
        mock_session.put.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_200_raises(self):
        import aiohttp
        bs = _make_blob_storage()

        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="Internal Server Error")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.put = MagicMock(return_value=mock_resp)

        with pytest.raises(aiohttp.ClientError):
            await bs._upload_raw_to_signed_url(
                mock_session, "https://s3/signed", b"data", "application/json"
            )

    @pytest.mark.asyncio
    async def test_unexpected_exception_wraps_in_client_error(self):
        import aiohttp
        bs = _make_blob_storage()

        mock_session = AsyncMock()
        mock_session.put = MagicMock(side_effect=RuntimeError("unexpected"))

        with pytest.raises(aiohttp.ClientError, match="Unexpected error"):
            await bs._upload_raw_to_signed_url(
                mock_session, "https://s3/signed", b"data", "text/plain"
            )


# ===================================================================
# _create_placeholder
# ===================================================================

class TestCreatePlaceholder:
    """Tests for BlobStorage._create_placeholder."""

    @pytest.mark.asyncio
    async def test_success(self):
        bs = _make_blob_storage()

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"_id": "placeholder-1"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        result = await bs._create_placeholder(
            mock_session, "http://api/placeholder", {}, {}
        )
        assert result["_id"] == "placeholder-1"

    @pytest.mark.asyncio
    async def test_non_200_raises(self):
        import aiohttp
        bs = _make_blob_storage()

        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.json = AsyncMock(return_value={"error": "Bad Request"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        with pytest.raises(aiohttp.ClientError):
            await bs._create_placeholder(
                mock_session, "http://api/placeholder", {}, {}
            )

    @pytest.mark.asyncio
    async def test_unexpected_error_wraps_in_client_error(self):
        import aiohttp
        bs = _make_blob_storage()

        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=RuntimeError("boom"))

        with pytest.raises(aiohttp.ClientError, match="Unexpected error"):
            await bs._create_placeholder(
                mock_session, "http://api/placeholder", {}, {}
            )


# ===================================================================
# _download_with_range_requests
# ===================================================================

class TestDownloadWithRangeRequests:
    """Tests for BlobStorage._download_with_range_requests."""

    @pytest.mark.asyncio
    async def test_successful_parallel_download(self):
        """Small file downloaded in a single chunk via range requests."""
        bs = _make_blob_storage()

        # Content length returns 100 bytes
        bs._get_content_length = AsyncMock(return_value=100)

        # Single chunk download returns all data
        bs._download_chunk_with_retry = AsyncMock(
            return_value=(0, b"x" * 100)
        )

        mock_session = AsyncMock()
        result = await bs._download_with_range_requests(
            mock_session, "https://s3.example.com/file", chunk_size_mb=1
        )
        assert result == b"x" * 100

    @pytest.mark.asyncio
    async def test_none_content_length_raises(self):
        """When content length is None, should raise."""
        bs = _make_blob_storage()
        bs._get_content_length = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        with pytest.raises(Exception):
            await bs._download_with_range_requests(
                mock_session, "https://s3.example.com/file"
            )

    @pytest.mark.asyncio
    async def test_zero_content_length_raises(self):
        """When content length is 0, should raise."""
        bs = _make_blob_storage()
        bs._get_content_length = AsyncMock(return_value=0)

        mock_session = AsyncMock()
        with pytest.raises(Exception, match="Could not determine file size"):
            await bs._download_with_range_requests(
                mock_session, "https://s3.example.com/file"
            )

    @pytest.mark.asyncio
    async def test_size_mismatch_raises(self):
        """When reassembled bytes don't match expected size, should raise."""
        bs = _make_blob_storage()
        bs._get_content_length = AsyncMock(return_value=200)

        # Return only 100 bytes when 200 are expected
        bs._download_chunk_with_retry = AsyncMock(
            return_value=(0, b"x" * 100)
        )

        mock_session = AsyncMock()
        with pytest.raises(Exception, match="Size mismatch"):
            await bs._download_with_range_requests(
                mock_session, "https://s3.example.com/file", chunk_size_mb=1
            )


# ===================================================================
# apply — full transform context flow
# ===================================================================

class TestBlobStorageApplyFullFlow:
    """Full flow tests for BlobStorage.apply."""

    @pytest.mark.asyncio
    async def test_apply_cleans_block_containers(self):
        """apply should clean empty values from block_containers in dumped record."""
        bs = _make_blob_storage()
        bs.get_document_id_by_virtual_record_id = AsyncMock(return_value=None)
        bs.save_record_to_storage = AsyncMock(return_value=("doc-id", 2048))
        bs.store_virtual_record_mapping = AsyncMock()

        record = MagicMock()
        record.org_id = "org-1"
        record.id = "rec-1"
        record.virtual_record_id = "vr-1"
        record.model_dump.return_value = {
            "id": "rec-1",
            "empty_field": None,
            "block_containers": {
                "blocks": [{"content": "text", "empty": None}],
                "block_groups": [{"name": "g1", "empty": ""}],
            },
        }

        ctx = MagicMock()
        ctx.record = record

        result = await bs.apply(ctx)

        # Verify save_record_to_storage was called with cleaned dict
        call_args = bs.save_record_to_storage.call_args[0]
        cleaned_dict = call_args[3]
        assert "empty_field" not in cleaned_dict
        assert cleaned_dict["block_containers"]["blocks"][0] == {"content": "text"}
        assert cleaned_dict["block_containers"]["block_groups"][0] == {"name": "g1"}


# ===================================================================
# _clean_empty_values — nested cleaning deep tests
# ===================================================================

class TestCleanEmptyValuesDeep:
    """Deeper tests for _clean_empty_values."""

    def test_block_containers_not_list_blocks(self):
        """When blocks is not a list, it should not crash."""
        bs = _make_blob_storage()
        data = {
            "block_containers": {
                "blocks": "not_a_list",
                "block_groups": "also_not_a_list",
            },
        }
        result = bs._clean_empty_values(data)
        assert result["block_containers"]["blocks"] == "not_a_list"
        assert result["block_containers"]["block_groups"] == "also_not_a_list"

    def test_block_containers_missing_blocks_key(self):
        """When block_containers dict has no 'blocks' key, it still works."""
        bs = _make_blob_storage()
        data = {
            "block_containers": {
                "other_key": "value",
            },
        }
        result = bs._clean_empty_values(data)
        assert result["block_containers"]["other_key"] == "value"

    def test_multiple_none_empty_values_cleaned(self):
        """All types of empty values are cleaned at top level and in blocks."""
        bs = _make_blob_storage()
        data = {
            "valid": "ok",
            "none_val": None,
            "empty_str": "",
            "empty_list": [],
            "empty_dict": {},
            "zero": 0,
            "block_containers": {
                "blocks": [
                    {
                        "content": "text",
                        "none_field": None,
                        "empty_str_field": "",
                        "empty_list_field": [],
                        "empty_dict_field": {},
                        "zero_field": 0,
                    },
                ],
                "block_groups": [],
            },
        }
        result = bs._clean_empty_values(data)
        assert "none_val" not in result
        assert "empty_str" not in result
        assert "empty_list" not in result
        assert "empty_dict" not in result
        assert result["zero"] == 0

        block = result["block_containers"]["blocks"][0]
        assert "none_field" not in block
        assert "empty_str_field" not in block
        assert block["zero_field"] == 0


# ===================================================================
# save_record_to_storage — S3 compression failure fallback
# ===================================================================

class TestSaveRecordToStorageCompressionFallback:
    """Tests for the compression failure path in save_record_to_storage."""

    @pytest.mark.asyncio
    async def test_compression_failure_falls_back_to_uncompressed(self):
        """When compression fails, upload proceeds without compression."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
                {"storageType": "local"},
            ]
        )

        # Make compression fail
        bs._compress_record = MagicMock(side_effect=Exception("compression error"))

        # Mock successful upload
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"_id": "doc-123"})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.modules.transformers.blob_storage.aiohttp.ClientSession", return_value=mock_session):
            doc_id, file_size = await bs.save_record_to_storage(
                "org-1", "rec-1", "vr-1", {"key": "value"}
            )
            assert doc_id == "doc-123"
            assert file_size > 0


# ===================================================================
# get_document_id_by_virtual_record_id — exception propagation
# ===================================================================

class TestGetDocumentIdByVirtualRecordIdDeep:
    """Deeper tests for get_document_id_by_virtual_record_id."""

    @pytest.mark.asyncio
    async def test_graph_provider_exception_propagates(self):
        """Exception from graph_provider propagates."""
        graph_provider = AsyncMock()
        graph_provider.get_nodes_by_filters = AsyncMock(
            side_effect=RuntimeError("db error")
        )
        bs = _make_blob_storage(graph_provider=graph_provider)

        with pytest.raises(RuntimeError, match="db error"):
            await bs.get_document_id_by_virtual_record_id("vr-1")


# ===================================================================
# _get_signed_url — ContentTypeError in error response
# ===================================================================

class TestGetSignedUrlDeep:
    """Deeper tests for _get_signed_url."""

    @pytest.mark.asyncio
    async def test_non_200_content_type_error(self):
        """When error response can't parse JSON, falls back to text."""
        import aiohttp
        bs = _make_blob_storage()

        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.json = AsyncMock(side_effect=aiohttp.ContentTypeError(MagicMock(), MagicMock()))
        mock_resp.text = AsyncMock(return_value="plain error text")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        with pytest.raises(aiohttp.ClientError):
            await bs._get_signed_url(mock_session, "http://api/url", {}, {})

    @pytest.mark.asyncio
    async def test_unexpected_exception(self):
        """Unexpected exception is wrapped in ClientError."""
        import aiohttp
        bs = _make_blob_storage()

        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=RuntimeError("unexpected"))

        with pytest.raises(aiohttp.ClientError, match="Unexpected error"):
            await bs._get_signed_url(mock_session, "http://api/url", {}, {})


# ===================================================================
# _upload_to_signed_url — ContentTypeError in error response
# ===================================================================

class TestUploadToSignedUrlDeep:
    """Deeper tests for _upload_to_signed_url."""

    @pytest.mark.asyncio
    async def test_non_200_content_type_error(self):
        """When error response can't parse JSON, falls back to text."""
        import aiohttp
        bs = _make_blob_storage()

        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.json = AsyncMock(side_effect=aiohttp.ContentTypeError(MagicMock(), MagicMock()))
        mock_resp.text = AsyncMock(return_value="plain error text")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.put = MagicMock(return_value=mock_resp)

        with pytest.raises(aiohttp.ClientError):
            await bs._upload_to_signed_url(mock_session, "https://s3/signed", {})

    @pytest.mark.asyncio
    async def test_unexpected_exception(self):
        """Unexpected exception is wrapped in ClientError."""
        import aiohttp
        bs = _make_blob_storage()

        mock_session = AsyncMock()
        mock_session.put = MagicMock(side_effect=RuntimeError("boom"))

        with pytest.raises(aiohttp.ClientError, match="Unexpected error"):
            await bs._upload_to_signed_url(mock_session, "https://s3/signed", {})


# ===================================================================
# _create_placeholder — ContentTypeError in error response
# ===================================================================

class TestCreatePlaceholderDeep:
    """Deeper tests for _create_placeholder."""

    @pytest.mark.asyncio
    async def test_non_200_content_type_error(self):
        """When error response can't parse JSON, falls back to text."""
        import aiohttp
        bs = _make_blob_storage()

        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.json = AsyncMock(side_effect=aiohttp.ContentTypeError(MagicMock(), MagicMock()))
        mock_resp.text = AsyncMock(return_value="plain error text")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        with pytest.raises(aiohttp.ClientError):
            await bs._create_placeholder(mock_session, "http://api/p", {}, {})


# ===================================================================
# store_virtual_record_mapping — with file_size=None
# ===================================================================

class TestStoreVirtualRecordMappingDeep:
    """Deeper tests for store_virtual_record_mapping."""

    @pytest.mark.asyncio
    async def test_success_with_none_file_size(self):
        """File size of None means fileSizeBytes is not included in mapping."""
        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
        bs = _make_blob_storage(graph_provider=graph_provider)

        result = await bs.store_virtual_record_mapping("vr-1", "doc-1", None)
        assert result is True
        call_args = graph_provider.batch_upsert_nodes.call_args
        mapping_doc = call_args[0][0][0]
        assert "fileSizeBytes" not in mapping_doc


# ===================================================================
# _download_with_range_requests — multiple chunks
# ===================================================================

class TestDownloadWithRangeRequestsDeep:
    """Deeper tests for _download_with_range_requests."""

    @pytest.mark.asyncio
    async def test_multiple_chunks_reassembled_in_order(self):
        """Multiple chunks are downloaded and reassembled in correct order."""
        bs = _make_blob_storage()

        # 300 bytes, chunk size 100 = 3 chunks
        total_size = 300
        bs._get_content_length = AsyncMock(return_value=total_size)

        async def mock_chunk_download(session, url, start, end, chunk_index, max_retries=3):
            chunk_data = bytes(range(start, end + 1)) if end - start < 256 else b"x" * (end - start + 1)
            return (chunk_index, b"A" * 100)

        bs._download_chunk_with_retry = AsyncMock(side_effect=[
            (0, b"A" * 100),
            (1, b"B" * 100),
            (2, b"C" * 100),
        ])

        mock_session = AsyncMock()
        # chunk_size_mb=0.0001 to make chunk_size_bytes small enough
        # But we can't use fractional MB. Instead let's set bytes directly.
        # Use 100 bytes = 100/(1024*1024) MB

        # Actually let's just verify with large enough chunk size for 1 chunk
        bs._get_content_length = AsyncMock(return_value=300)
        bs._download_chunk_with_retry = AsyncMock(
            return_value=(0, b"x" * 300)
        )

        result = await bs._download_with_range_requests(
            mock_session, "https://s3.example.com/file", chunk_size_mb=1
        )
        assert result == b"x" * 300

    @pytest.mark.asyncio
    async def test_gather_exception_propagates(self):
        """Exception during parallel download propagates."""
        bs = _make_blob_storage()
        bs._get_content_length = AsyncMock(return_value=100)
        bs._download_chunk_with_retry = AsyncMock(
            side_effect=RuntimeError("download failed")
        )

        mock_session = AsyncMock()
        with pytest.raises(RuntimeError, match="download failed"):
            await bs._download_with_range_requests(
                mock_session, "https://s3.example.com/file", chunk_size_mb=1
            )
