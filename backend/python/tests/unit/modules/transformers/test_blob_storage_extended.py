"""
Extended tests for BlobStorage — targets uncovered lines:
604-606, 620-621, 651, 668-669, 681-682, 696, 708-714, 814, 871,
878-887, 900-917, 936-937, 1017-1134.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest


def _make_blob_storage(graph_provider=None):
    from app.modules.transformers.blob_storage import BlobStorage

    logger = MagicMock()
    config_service = AsyncMock()
    return BlobStorage(logger, config_service, graph_provider)


# ===================================================================
# save_record_to_storage — local upload error branches
# ===================================================================


class TestSaveRecordLocalUploadErrors:
    """Cover lines 604-606, 620-621: ContentTypeError on local upload non-200."""

    @pytest.mark.asyncio
    async def test_local_upload_non_200_content_type_error(self):
        """When local upload returns non-200 and response.json() raises ContentTypeError."""
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
        mock_resp.json = AsyncMock(
            side_effect=aiohttp.ContentTypeError(MagicMock(), MagicMock())
        )
        mock_resp.text = AsyncMock(return_value="Server Error HTML")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            with pytest.raises(Exception, match="Failed to upload record"):
                await bs.save_record_to_storage(
                    "org-1", "rec-1", "vr-1", {"key": "value"}
                )

    @pytest.mark.asyncio
    async def test_local_upload_aiohttp_client_error(self):
        """aiohttp.ClientError during local upload => lines 619-621."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
                {"storageType": "local"},
            ]
        )

        mock_session = AsyncMock()
        mock_session.post = MagicMock(
            side_effect=aiohttp.ClientError("Connection reset")
        )
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            with pytest.raises(aiohttp.ClientError):
                await bs.save_record_to_storage(
                    "org-1", "rec-1", "vr-1", {"key": "value"}
                )


# ===================================================================
# save_record_to_storage — S3 path: no placeholder document_id, no signed url
# ===================================================================


class TestSaveRecordS3ErrorBranches:
    """Cover lines 651, 668-669, 681-682, 696, 708-714."""

    @pytest.mark.asyncio
    async def test_s3_no_placeholder_document_id(self):
        """Placeholder response missing _id => raises."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
                {"storageType": "s3"},
            ]
        )

        placeholder_resp = AsyncMock()
        placeholder_resp.status = 200
        placeholder_resp.json = AsyncMock(return_value={})  # No _id
        placeholder_resp.__aenter__ = AsyncMock(return_value=placeholder_resp)
        placeholder_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=placeholder_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            with pytest.raises(Exception, match="No document ID in placeholder"):
                await bs.save_record_to_storage(
                    "org-1", "rec-1", "vr-1", {"key": "value"}
                )

    @pytest.mark.asyncio
    async def test_s3_no_signed_url_in_response(self):
        """Signed URL endpoint returns no signedUrl => raises."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
                {"storageType": "s3"},
            ]
        )

        placeholder_resp = AsyncMock()
        placeholder_resp.status = 200
        placeholder_resp.json = AsyncMock(return_value={"_id": "doc-1"})
        placeholder_resp.__aenter__ = AsyncMock(return_value=placeholder_resp)
        placeholder_resp.__aexit__ = AsyncMock(return_value=False)

        signed_url_resp = AsyncMock()
        signed_url_resp.status = 200
        signed_url_resp.json = AsyncMock(return_value={})  # No signedUrl
        signed_url_resp.__aenter__ = AsyncMock(return_value=signed_url_resp)
        signed_url_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(
            side_effect=[placeholder_resp, signed_url_resp]
        )
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            with pytest.raises(Exception, match="No signed URL"):
                await bs.save_record_to_storage(
                    "org-1", "rec-1", "vr-1", {"key": "value"}
                )

    @pytest.mark.asyncio
    async def test_s3_uncompressed_fallback(self):
        """When compression fails, uses uncompressed S3 upload."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
                {"storageType": "s3"},
            ]
        )
        # Force compression to fail
        bs._compress_record = MagicMock(side_effect=Exception("Compression failed"))

        placeholder_resp = AsyncMock()
        placeholder_resp.status = 200
        placeholder_resp.json = AsyncMock(return_value={"_id": "doc-uncompressed"})
        placeholder_resp.__aenter__ = AsyncMock(return_value=placeholder_resp)
        placeholder_resp.__aexit__ = AsyncMock(return_value=False)

        signed_url_resp = AsyncMock()
        signed_url_resp.status = 200
        signed_url_resp.json = AsyncMock(
            return_value={"signedUrl": "https://s3/upload"}
        )
        signed_url_resp.__aenter__ = AsyncMock(return_value=signed_url_resp)
        signed_url_resp.__aexit__ = AsyncMock(return_value=False)

        upload_resp = AsyncMock()
        upload_resp.status = 200
        upload_resp.__aenter__ = AsyncMock(return_value=upload_resp)
        upload_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(
            side_effect=[placeholder_resp, signed_url_resp]
        )
        mock_session.put = MagicMock(return_value=upload_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            doc_id, file_size = await bs.save_record_to_storage(
                "org-1", "rec-1", "vr-1", {"key": "value"}
            )
            assert doc_id == "doc-uncompressed"
            assert file_size > 0

    @pytest.mark.asyncio
    async def test_s3_client_error_propagates(self):
        """aiohttp.ClientError during S3 storage process => lines 708-710."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
                {"storageType": "s3"},
            ]
        )

        mock_session = AsyncMock()
        mock_session.post = MagicMock(
            side_effect=aiohttp.ClientError("Connection lost")
        )
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            with pytest.raises(aiohttp.ClientError):
                await bs.save_record_to_storage(
                    "org-1", "rec-1", "vr-1", {"key": "value"}
                )

    @pytest.mark.asyncio
    async def test_s3_unexpected_error_propagates(self):
        """Non-aiohttp exception during S3 path => lines 711-714.
        _create_placeholder wraps RuntimeError in aiohttp.ClientError."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
                {"storageType": "s3"},
            ]
        )

        mock_session = AsyncMock()
        mock_session.post = MagicMock(
            side_effect=RuntimeError("Unexpected error")
        )
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            with pytest.raises((aiohttp.ClientError, RuntimeError)):
                await bs.save_record_to_storage(
                    "org-1", "rec-1", "vr-1", {"key": "value"}
                )


# ===================================================================
# get_record_from_storage — line 814, 871, 878-887, 900-917, 936-937
# ===================================================================


class TestGetRecordFromStorageExtended:
    """Cover: no document (814), parallel download (871+878-887),
    fallback on parallel failure (900-917), no record in signed URL data (936-937)."""

    @pytest.mark.asyncio
    async def test_no_document_id_returns_none(self):
        """Line 814: document_id is None => return None."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
            ]
        )
        bs.get_document_id_by_virtual_record_id = AsyncMock(
            return_value=(None, None)
        )

        result = await bs.get_record_from_storage("vr-1", "org-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_signed_url_parallel_download_success(self):
        """Lines 871+878-887: file_size_bytes is None => parallel download."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
            ]
        )
        bs.get_document_id_by_virtual_record_id = AsyncMock(
            return_value=("doc-123", None)  # None file_size => parallel
        )

        record_data = {"id": "rec-1", "content": "hello"}

        # First response: signedUrl
        first_resp = AsyncMock()
        first_resp.status = 200
        first_resp.json = AsyncMock(
            return_value={"signedUrl": "https://s3/signed"}
        )
        first_resp.__aenter__ = AsyncMock(return_value=first_resp)
        first_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=first_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        file_bytes = json.dumps(
            {"record": record_data, "isCompressed": False}
        ).encode("utf-8")

        bs._download_with_range_requests = AsyncMock(return_value=file_bytes)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            result = await bs.get_record_from_storage("vr-1", "org-1")
            assert result == record_data

    @pytest.mark.asyncio
    async def test_signed_url_parallel_download_fails_fallback_success(self):
        """Lines 900-917: parallel download fails, fallback single download succeeds."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
            ]
        )
        bs.get_document_id_by_virtual_record_id = AsyncMock(
            return_value=("doc-123", None)  # None => parallel
        )

        record_data = {"id": "rec-1", "content": "hello"}

        # First response: signedUrl
        first_resp = AsyncMock()
        first_resp.status = 200
        first_resp.json = AsyncMock(
            return_value={"signedUrl": "https://s3/signed"}
        )
        first_resp.__aenter__ = AsyncMock(return_value=first_resp)
        first_resp.__aexit__ = AsyncMock(return_value=False)

        # Fallback response
        fallback_resp = AsyncMock()
        fallback_resp.status = 200
        fallback_resp.json = AsyncMock(
            return_value={"record": record_data, "isCompressed": False}
        )
        fallback_resp.__aenter__ = AsyncMock(return_value=fallback_resp)
        fallback_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=[first_resp, fallback_resp])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        # Parallel download fails
        bs._download_with_range_requests = AsyncMock(
            side_effect=Exception("Range request not supported")
        )

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            result = await bs.get_record_from_storage("vr-1", "org-1")
            assert result == record_data

    @pytest.mark.asyncio
    async def test_signed_url_parallel_and_fallback_both_fail(self):
        """Lines 912-914: both parallel and fallback downloads fail."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
            ]
        )
        bs.get_document_id_by_virtual_record_id = AsyncMock(
            return_value=("doc-123", None)
        )

        first_resp = AsyncMock()
        first_resp.status = 200
        first_resp.json = AsyncMock(
            return_value={"signedUrl": "https://s3/signed"}
        )
        first_resp.__aenter__ = AsyncMock(return_value=first_resp)
        first_resp.__aexit__ = AsyncMock(return_value=False)

        fallback_resp = AsyncMock()
        fallback_resp.status = 500
        fallback_resp.__aenter__ = AsyncMock(return_value=fallback_resp)
        fallback_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=[first_resp, fallback_resp])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        bs._download_with_range_requests = AsyncMock(
            side_effect=Exception("Parallel failed")
        )

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            with pytest.raises(Exception, match="Both parallel and fallback"):
                await bs.get_record_from_storage("vr-1", "org-1")

    @pytest.mark.asyncio
    async def test_signed_url_single_download_non_200_raises(self):
        """Lines 898-899+916-917: small file, single download fails."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
            ]
        )
        # Small file => single download (not parallel)
        bs.get_document_id_by_virtual_record_id = AsyncMock(
            return_value=("doc-123", 100)
        )

        first_resp = AsyncMock()
        first_resp.status = 200
        first_resp.json = AsyncMock(
            return_value={"signedUrl": "https://s3/signed"}
        )
        first_resp.__aenter__ = AsyncMock(return_value=first_resp)
        first_resp.__aexit__ = AsyncMock(return_value=False)

        signed_resp = AsyncMock()
        signed_resp.status = 500
        signed_resp.__aenter__ = AsyncMock(return_value=signed_resp)
        signed_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=[first_resp, signed_resp])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            with pytest.raises(Exception, match="Failed to retrieve record"):
                await bs.get_record_from_storage("vr-1", "org-1")

    @pytest.mark.asyncio
    async def test_signed_url_data_has_no_record_field(self):
        """Lines 936-937: signed URL data returned but no 'record' key."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
            ]
        )
        bs.get_document_id_by_virtual_record_id = AsyncMock(
            return_value=("doc-123", 100)
        )

        first_resp = AsyncMock()
        first_resp.status = 200
        first_resp.json = AsyncMock(
            return_value={"signedUrl": "https://s3/signed"}
        )
        first_resp.__aenter__ = AsyncMock(return_value=first_resp)
        first_resp.__aexit__ = AsyncMock(return_value=False)

        # Signed URL response has no "record" key
        signed_resp = AsyncMock()
        signed_resp.status = 200
        signed_resp.json = AsyncMock(return_value={"something": "else"})
        signed_resp.__aenter__ = AsyncMock(return_value=signed_resp)
        signed_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=[first_resp, signed_resp])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            with pytest.raises(Exception, match="No record found"):
                await bs.get_record_from_storage("vr-1", "org-1")

    @pytest.mark.asyncio
    async def test_large_file_parallel_download(self):
        """Lines 871-874: file_size_bytes >= 3MB => use parallel download."""
        bs = _make_blob_storage()
        bs.config_service.get_config = AsyncMock(
            side_effect=[
                {"scopedJwtSecret": "secret"},
                {"cm": {"endpoint": "http://localhost:3001"}},
            ]
        )
        # Large file
        bs.get_document_id_by_virtual_record_id = AsyncMock(
            return_value=("doc-123", 5 * 1024 * 1024)
        )

        record_data = {"id": "rec-1", "content": "large"}

        first_resp = AsyncMock()
        first_resp.status = 200
        first_resp.json = AsyncMock(
            return_value={"signedUrl": "https://s3/signed"}
        )
        first_resp.__aenter__ = AsyncMock(return_value=first_resp)
        first_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=first_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        file_bytes = json.dumps(
            {"record": record_data, "isCompressed": False}
        ).encode("utf-8")
        bs._download_with_range_requests = AsyncMock(return_value=file_bytes)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            result = await bs.get_record_from_storage("vr-1", "org-1")
            assert result == record_data
            bs._download_with_range_requests.assert_awaited_once()


# ===================================================================
# save_conversation_file_to_storage — lines 1017-1134
# ===================================================================


class TestSaveConversationFileToStorage:
    """Cover save_conversation_file_to_storage local and S3 paths."""

    @pytest.mark.asyncio
    async def test_local_upload_success(self):
        """Local storage upload succeeds and returns download URL."""
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=(
                {"Authorization": "Bearer tok"},
                "http://localhost:3001",
                "local",
            )
        )

        upload_resp = AsyncMock()
        upload_resp.status = 200
        upload_resp.json = AsyncMock(return_value={"_id": "local-doc-1"})
        upload_resp.__aenter__ = AsyncMock(return_value=upload_resp)
        upload_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=upload_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            result = await bs.save_conversation_file_to_storage(
                "org-1", "conv-1", "data.csv", b"col1,col2\n1,2\n"
            )
            assert result["documentId"] == "local-doc-1"
            assert result["fileName"] == "data.csv"
            assert "downloadUrl" in result

    @pytest.mark.asyncio
    async def test_local_upload_non_200_raises(self):
        """Local upload returns non-200."""
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=(
                {"Authorization": "Bearer tok"},
                "http://localhost:3001",
                "local",
            )
        )

        upload_resp = AsyncMock()
        upload_resp.status = 500
        upload_resp.json = AsyncMock(return_value={"error": "fail"})
        upload_resp.__aenter__ = AsyncMock(return_value=upload_resp)
        upload_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=upload_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            with pytest.raises(Exception, match="Local upload failed"):
                await bs.save_conversation_file_to_storage(
                    "org-1", "conv-1", "data.csv", b"col1,col2\n"
                )

    @pytest.mark.asyncio
    async def test_local_upload_non_200_json_parse_error(self):
        """Local upload returns non-200 and json() raises."""
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=(
                {"Authorization": "Bearer tok"},
                "http://localhost:3001",
                "local",
            )
        )

        upload_resp = AsyncMock()
        upload_resp.status = 500
        upload_resp.json = AsyncMock(side_effect=Exception("not json"))
        upload_resp.text = AsyncMock(return_value="Internal Server Error")
        upload_resp.__aenter__ = AsyncMock(return_value=upload_resp)
        upload_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=upload_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            with pytest.raises(Exception, match="Local upload failed"):
                await bs.save_conversation_file_to_storage(
                    "org-1", "conv-1", "data.csv", b"col1,col2\n"
                )

    @pytest.mark.asyncio
    async def test_local_upload_no_document_id(self):
        """Local upload succeeds but no _id in response."""
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=(
                {"Authorization": "Bearer tok"},
                "http://localhost:3001",
                "local",
            )
        )

        upload_resp = AsyncMock()
        upload_resp.status = 200
        upload_resp.json = AsyncMock(return_value={})  # no _id
        upload_resp.__aenter__ = AsyncMock(return_value=upload_resp)
        upload_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=upload_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            with pytest.raises(Exception, match="No document ID"):
                await bs.save_conversation_file_to_storage(
                    "org-1", "conv-1", "data.csv", b"col1,col2\n"
                )

    @pytest.mark.asyncio
    async def test_s3_upload_success_with_signed_url(self):
        """S3 path: placeholder -> signed URL -> upload -> get download signed URL."""
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=(
                {"Authorization": "Bearer tok"},
                "http://localhost:3001",
                "s3",
            )
        )

        placeholder_resp = AsyncMock()
        placeholder_resp.status = 200
        placeholder_resp.json = AsyncMock(return_value={"_id": "s3-doc-1"})
        placeholder_resp.__aenter__ = AsyncMock(return_value=placeholder_resp)
        placeholder_resp.__aexit__ = AsyncMock(return_value=False)

        bs._create_placeholder = AsyncMock(return_value={"_id": "s3-doc-1"})
        bs._get_signed_url = AsyncMock(
            return_value={"signedUrl": "https://s3/upload-signed"}
        )
        bs._upload_raw_to_signed_url = AsyncMock()

        download_resp = AsyncMock()
        download_resp.status = 200
        download_resp.json = AsyncMock(
            return_value={"signedUrl": "https://s3/download-signed"}
        )
        download_resp.__aenter__ = AsyncMock(return_value=download_resp)
        download_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=download_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            result = await bs.save_conversation_file_to_storage(
                "org-1", "conv-1", "data.csv", b"col1,col2\n"
            )
            assert result["documentId"] == "s3-doc-1"
            assert result["signedUrl"] == "https://s3/download-signed"
            assert result["fileName"] == "data.csv"

    @pytest.mark.asyncio
    async def test_s3_upload_no_placeholder_document_id(self):
        """S3 path: placeholder response missing _id."""
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=(
                {"Authorization": "Bearer tok"},
                "http://localhost:3001",
                "s3",
            )
        )

        bs._create_placeholder = AsyncMock(return_value={})  # No _id

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            with pytest.raises(Exception, match="No document ID"):
                await bs.save_conversation_file_to_storage(
                    "org-1", "conv-1", "data.csv", b"col1,col2\n"
                )

    @pytest.mark.asyncio
    async def test_s3_upload_no_signed_url(self):
        """S3 path: signed URL response missing signedUrl key."""
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=(
                {"Authorization": "Bearer tok"},
                "http://localhost:3001",
                "s3",
            )
        )

        bs._create_placeholder = AsyncMock(return_value={"_id": "s3-doc-1"})
        bs._get_signed_url = AsyncMock(return_value={})  # No signedUrl

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            with pytest.raises(Exception, match="No signed URL"):
                await bs.save_conversation_file_to_storage(
                    "org-1", "conv-1", "data.csv", b"col1,col2\n"
                )

    @pytest.mark.asyncio
    async def test_s3_upload_fallback_url(self):
        """S3 path: download signed URL not available => uses external URL fallback."""
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=(
                {"Authorization": "Bearer tok"},
                "http://localhost:3001",
                "s3",
            )
        )

        bs._create_placeholder = AsyncMock(return_value={"_id": "s3-doc-1"})
        bs._get_signed_url = AsyncMock(
            return_value={"signedUrl": "https://s3/upload-signed"}
        )
        bs._upload_raw_to_signed_url = AsyncMock()

        download_resp = AsyncMock()
        download_resp.status = 200
        download_resp.json = AsyncMock(return_value={})  # No signedUrl
        download_resp.__aenter__ = AsyncMock(return_value=download_resp)
        download_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=download_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            result = await bs.save_conversation_file_to_storage(
                "org-1", "conv-1", "data.csv", b"col1,col2\n"
            )
            assert result["documentId"] == "s3-doc-1"
            assert "downloadUrl" in result
            assert result["fileName"] == "data.csv"

    @pytest.mark.asyncio
    async def test_s3_download_non_200_falls_through(self):
        """S3 path: download endpoint returns non-200 => falls to fallback URL."""
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=(
                {"Authorization": "Bearer tok"},
                "http://localhost:3001",
                "s3",
            )
        )

        bs._create_placeholder = AsyncMock(return_value={"_id": "s3-doc-1"})
        bs._get_signed_url = AsyncMock(
            return_value={"signedUrl": "https://s3/upload-signed"}
        )
        bs._upload_raw_to_signed_url = AsyncMock()

        download_resp = AsyncMock()
        download_resp.status = 403
        download_resp.__aenter__ = AsyncMock(return_value=download_resp)
        download_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=download_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            result = await bs.save_conversation_file_to_storage(
                "org-1", "conv-1", "data.csv", b"col1,col2\n"
            )
            assert result["documentId"] == "s3-doc-1"
            assert "downloadUrl" in result

    @pytest.mark.asyncio
    async def test_exception_propagated(self):
        """Top-level exception in save_conversation_file_to_storage is re-raised."""
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            side_effect=ValueError("Bad config")
        )

        with pytest.raises(ValueError, match="Bad config"):
            await bs.save_conversation_file_to_storage(
                "org-1", "conv-1", "data.csv", b"col1,col2\n"
            )
