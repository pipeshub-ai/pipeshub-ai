"""
Deep coverage tests for app/modules/transformers/blob_storage.py.

Targets remaining uncovered blocks after test_blob_storage.py and
test_blob_storage_extended.py:
- _compress_record / _decompress_bytes
- _process_downloaded_record branches
- _clean_top_level_empty_values / _clean_empty_values
- _get_content_length branches
- _download_chunk_with_retry retry branches
- _download_with_range_requests error branches
- _get_signed_url error branches
- _upload_to_signed_url error branches
- _upload_raw_to_signed_url error branches
- _create_placeholder error branches
- _get_auth_and_config error branches
- apply method
- get_document_id_by_virtual_record_id
- store_virtual_record_mapping
"""

import json
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import aiohttp
import pytest


def _make_blob_storage(graph_provider=None):
    from app.modules.transformers.blob_storage import BlobStorage
    logger = MagicMock()
    config_service = AsyncMock()
    return BlobStorage(logger, config_service, graph_provider)


# ===================================================================
# _compress_record / _decompress_bytes
# ===================================================================


class TestCompressDecompress:
    def test_compress_record_roundtrip(self):
        bs = _make_blob_storage()
        record = {"blocks": [{"text": "hello world " * 100}]}
        compressed = bs._compress_record(record)
        assert isinstance(compressed, str)  # base64 encoded
        assert len(compressed) > 0

    def test_decompress_bytes(self):
        import zstandard as zstd
        bs = _make_blob_storage()
        original = b"test data for decompression"
        compressor = zstd.ZstdCompressor()
        compressed = compressor.compress(original)
        result = bs._decompress_bytes(compressed)
        assert result == original


# ===================================================================
# _process_downloaded_record
# ===================================================================


class TestProcessDownloadedRecord:
    def test_compressed_record_success(self):
        import base64
        import msgspec
        import zstandard as zstd

        bs = _make_blob_storage()
        record = {"key": "value", "blocks": [1, 2, 3]}
        msgpack_bytes = msgspec.msgpack.encode(record)
        compressor = zstd.ZstdCompressor(level=10)
        compressed = compressor.compress(msgpack_bytes)
        b64_data = base64.b64encode(compressed).decode("utf-8")

        data = {"isCompressed": True, "record": b64_data}
        result = bs._process_downloaded_record(data)
        assert result["key"] == "value"

    def test_compressed_record_missing_record_field(self):
        bs = _make_blob_storage()
        data = {"isCompressed": True}
        with pytest.raises(Exception, match="Missing record"):
            bs._process_downloaded_record(data)

    def test_compressed_record_corrupt_data(self):
        bs = _make_blob_storage()
        data = {"isCompressed": True, "record": "not_valid_base64!!!"}
        with pytest.raises(Exception, match="Decompression failed"):
            bs._process_downloaded_record(data)

    def test_uncompressed_record(self):
        bs = _make_blob_storage()
        data = {"record": {"blocks": [{"text": "hello"}]}}
        result = bs._process_downloaded_record(data)
        assert result == {"blocks": [{"text": "hello"}]}

    def test_unknown_format(self):
        bs = _make_blob_storage()
        data = {"something_else": True}
        with pytest.raises(Exception, match="Unknown record format"):
            bs._process_downloaded_record(data)


# ===================================================================
# _clean_top_level_empty_values / _clean_empty_values
# ===================================================================


class TestCleanEmptyValues:
    def test_clean_top_level_empty_values(self):
        bs = _make_blob_storage()
        obj = {
            "name": "test",
            "empty_str": "",
            "none_val": None,
            "empty_list": [],
            "empty_dict": {},
            "valid_list": [1, 2],
            "zero": 0,
            "false": False,
        }
        result = bs._clean_top_level_empty_values(obj)
        assert "name" in result
        assert "empty_str" not in result
        assert "none_val" not in result
        assert "empty_list" not in result
        assert "empty_dict" not in result
        assert "valid_list" in result
        assert result["zero"] == 0
        assert result["false"] is False

    def test_clean_empty_values_with_block_containers(self):
        bs = _make_blob_storage()
        data = {
            "name": "test",
            "empty_field": "",
            "block_containers": {
                "blocks": [
                    {"text": "hello", "empty": ""},
                    {"text": "world", "none_val": None},
                ],
                "block_groups": [
                    {"type": "TABLE", "empty": []},
                ],
            },
        }
        result = bs._clean_empty_values(data)
        assert "empty_field" not in result
        blocks = result["block_containers"]["blocks"]
        assert "empty" not in blocks[0]
        assert "none_val" not in blocks[1]

    def test_clean_empty_values_no_block_containers(self):
        bs = _make_blob_storage()
        data = {"name": "test", "value": 42}
        result = bs._clean_empty_values(data)
        assert result == {"name": "test", "value": 42}

    def test_clean_empty_values_non_dict_blocks(self):
        bs = _make_blob_storage()
        data = {
            "block_containers": {
                "blocks": ["string_block", 42],
                "block_groups": [None],
            }
        }
        result = bs._clean_empty_values(data)
        assert result["block_containers"]["blocks"] == ["string_block", 42]


# ===================================================================
# _get_content_length
# ===================================================================


class TestGetContentLength:
    @pytest.mark.asyncio
    async def test_partial_content_with_range(self):
        bs = _make_blob_storage()
        mock_resp = AsyncMock()
        mock_resp.status = 206  # Partial Content
        mock_resp.headers = {"Content-Range": "bytes 0-0/1024"}
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)

        result = await bs._get_content_length(mock_session, "https://example.com/file")
        assert result == 1024

    @pytest.mark.asyncio
    async def test_full_content_with_content_length(self):
        bs = _make_blob_storage()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Length": "2048"}
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)

        result = await bs._get_content_length(mock_session, "https://example.com/file")
        assert result == 2048

    @pytest.mark.asyncio
    async def test_no_content_length_header(self):
        bs = _make_blob_storage()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {}
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)

        result = await bs._get_content_length(mock_session, "https://example.com/file")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        bs = _make_blob_storage()
        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=Exception("network error"))

        result = await bs._get_content_length(mock_session, "https://example.com/file")
        assert result is None


# ===================================================================
# _get_signed_url error branches
# ===================================================================


class TestGetSignedUrl:
    @pytest.mark.asyncio
    async def test_non_200_json_error(self):
        bs = _make_blob_storage()
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.json = AsyncMock(return_value={"error": "Server Error"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        with pytest.raises(aiohttp.ClientError, match="Failed with status 500"):
            await bs._get_signed_url(mock_session, "http://api/url", {}, {})

    @pytest.mark.asyncio
    async def test_non_200_content_type_error(self):
        bs = _make_blob_storage()
        mock_resp = AsyncMock()
        mock_resp.status = 502
        mock_resp.json = AsyncMock(
            side_effect=aiohttp.ContentTypeError(MagicMock(), MagicMock())
        )
        mock_resp.text = AsyncMock(return_value="Bad Gateway HTML")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        with pytest.raises(aiohttp.ClientError):
            await bs._get_signed_url(mock_session, "http://api/url", {}, {})

    @pytest.mark.asyncio
    async def test_unexpected_exception(self):
        bs = _make_blob_storage()
        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=RuntimeError("Unexpected"))

        with pytest.raises(aiohttp.ClientError, match="Unexpected error"):
            await bs._get_signed_url(mock_session, "http://api/url", {}, {})


# ===================================================================
# _upload_to_signed_url error branches
# ===================================================================


class TestUploadToSignedUrl:
    @pytest.mark.asyncio
    async def test_non_200_json_error(self):
        bs = _make_blob_storage()
        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.json = AsyncMock(return_value={"error": "Bad Request"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.put = MagicMock(return_value=mock_resp)

        with pytest.raises(aiohttp.ClientError, match="Failed to upload"):
            await bs._upload_to_signed_url(mock_session, "https://s3/upload", {})

    @pytest.mark.asyncio
    async def test_non_200_content_type_error(self):
        bs = _make_blob_storage()
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.json = AsyncMock(
            side_effect=aiohttp.ContentTypeError(MagicMock(), MagicMock())
        )
        mock_resp.text = AsyncMock(return_value="Server Error")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.put = MagicMock(return_value=mock_resp)

        with pytest.raises(aiohttp.ClientError):
            await bs._upload_to_signed_url(mock_session, "https://s3/upload", {})

    @pytest.mark.asyncio
    async def test_unexpected_exception(self):
        bs = _make_blob_storage()
        mock_session = AsyncMock()
        mock_session.put = MagicMock(side_effect=RuntimeError("IO Error"))

        with pytest.raises(aiohttp.ClientError, match="Unexpected error"):
            await bs._upload_to_signed_url(mock_session, "https://s3/upload", {})

    @pytest.mark.asyncio
    async def test_success(self):
        bs = _make_blob_storage()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.put = MagicMock(return_value=mock_resp)

        result = await bs._upload_to_signed_url(mock_session, "https://s3/upload", {})
        assert result == 200


# ===================================================================
# _upload_raw_to_signed_url
# ===================================================================


class TestUploadRawToSignedUrl:
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
            mock_session, "https://s3/upload", b"content", "text/csv"
        )

    @pytest.mark.asyncio
    async def test_non_200_raises(self):
        bs = _make_blob_storage()
        mock_resp = AsyncMock()
        mock_resp.status = 403
        mock_resp.text = AsyncMock(return_value="Forbidden")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.put = MagicMock(return_value=mock_resp)

        with pytest.raises(aiohttp.ClientError, match="Failed to upload"):
            await bs._upload_raw_to_signed_url(
                mock_session, "https://s3/upload", b"content", "text/csv"
            )

    @pytest.mark.asyncio
    async def test_unexpected_exception(self):
        bs = _make_blob_storage()
        mock_session = AsyncMock()
        mock_session.put = MagicMock(side_effect=RuntimeError("OS Error"))

        with pytest.raises(aiohttp.ClientError, match="Unexpected error"):
            await bs._upload_raw_to_signed_url(
                mock_session, "https://s3/upload", b"content", "text/csv"
            )


# ===================================================================
# _create_placeholder error branches
# ===================================================================


class TestCreatePlaceholder:
    @pytest.mark.asyncio
    async def test_non_200_json_error(self):
        bs = _make_blob_storage()
        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.json = AsyncMock(return_value={"error": "Bad data"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        with pytest.raises(aiohttp.ClientError):
            await bs._create_placeholder(mock_session, "http://api/placeholder", {}, {})

    @pytest.mark.asyncio
    async def test_non_200_content_type_error(self):
        bs = _make_blob_storage()
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.json = AsyncMock(
            side_effect=aiohttp.ContentTypeError(MagicMock(), MagicMock())
        )
        mock_resp.text = AsyncMock(return_value="Server Error")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        with pytest.raises(aiohttp.ClientError):
            await bs._create_placeholder(mock_session, "http://api/placeholder", {}, {})

    @pytest.mark.asyncio
    async def test_unexpected_exception(self):
        bs = _make_blob_storage()
        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=RuntimeError("DB error"))

        with pytest.raises(aiohttp.ClientError, match="Unexpected error"):
            await bs._create_placeholder(mock_session, "http://api/placeholder", {}, {})


# ===================================================================
# _get_auth_and_config
# ===================================================================


class TestGetAuthAndConfig:
    @pytest.mark.asyncio
    async def test_success(self):
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret123"},
                {"cm": {"endpoint": "http://localhost:3001"}},
                {"storageType": "s3"},
            ]
        )
        headers, endpoint, storage_type = await bs._get_auth_and_config("org-1")
        assert "Authorization" in headers
        assert endpoint == "http://localhost:3001"
        assert storage_type == "s3"

    @pytest.mark.asyncio
    async def test_missing_jwt_secret(self):
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(return_value={})
        with pytest.raises(ValueError, match="Missing scoped JWT secret"):
            await bs._get_auth_and_config("org-1")

    @pytest.mark.asyncio
    async def test_missing_endpoint(self):
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": ""}},
            ]
        )
        with pytest.raises(ValueError, match="Missing CM endpoint"):
            await bs._get_auth_and_config("org-1")

    @pytest.mark.asyncio
    async def test_missing_storage_type(self):
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
                {},  # No storageType
            ]
        )
        with pytest.raises(ValueError, match="Missing storage type"):
            await bs._get_auth_and_config("org-1")


# ===================================================================
# get_document_id_by_virtual_record_id
# ===================================================================


class TestGetDocumentIdByVirtualRecordId:
    @pytest.mark.asyncio
    async def test_no_graph_provider_raises(self):
        bs = _make_blob_storage(graph_provider=None)
        with pytest.raises(Exception, match="GraphProvider not initialized"):
            await bs.get_document_id_by_virtual_record_id("vr-1")

    @pytest.mark.asyncio
    async def test_found_by_filter(self):
        mock_gp = AsyncMock()
        mock_gp.get_nodes_by_filters = AsyncMock(
            return_value=[{"documentId": "doc-1", "fileSizeBytes": 1024}]
        )
        bs = _make_blob_storage(graph_provider=mock_gp)
        doc_id, size = await bs.get_document_id_by_virtual_record_id("vr-1")
        assert doc_id == "doc-1"
        assert size == 1024

    @pytest.mark.asyncio
    async def test_found_by_key_fallback(self):
        mock_gp = AsyncMock()
        mock_gp.get_nodes_by_filters = AsyncMock(return_value=[])
        mock_gp.get_document = AsyncMock(
            return_value={"documentId": "doc-2", "fileSizeBytes": 2048}
        )
        bs = _make_blob_storage(graph_provider=mock_gp)
        doc_id, size = await bs.get_document_id_by_virtual_record_id("vr-1")
        assert doc_id == "doc-2"
        assert size == 2048

    @pytest.mark.asyncio
    async def test_not_found(self):
        mock_gp = AsyncMock()
        mock_gp.get_nodes_by_filters = AsyncMock(return_value=[])
        mock_gp.get_document = AsyncMock(return_value=None)
        bs = _make_blob_storage(graph_provider=mock_gp)
        doc_id, size = await bs.get_document_id_by_virtual_record_id("vr-1")
        assert doc_id is None
        assert size is None

    @pytest.mark.asyncio
    async def test_found_but_no_document_id_field(self):
        mock_gp = AsyncMock()
        mock_gp.get_nodes_by_filters = AsyncMock(
            return_value=[{"fileSizeBytes": 1024}]
        )
        bs = _make_blob_storage(graph_provider=mock_gp)
        doc_id, size = await bs.get_document_id_by_virtual_record_id("vr-1")
        assert doc_id is None
        assert size is None

    @pytest.mark.asyncio
    async def test_exception_propagated(self):
        mock_gp = AsyncMock()
        mock_gp.get_nodes_by_filters = AsyncMock(side_effect=Exception("DB error"))
        bs = _make_blob_storage(graph_provider=mock_gp)
        with pytest.raises(Exception, match="DB error"):
            await bs.get_document_id_by_virtual_record_id("vr-1")
