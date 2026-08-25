"""Tests for uncovered ranges in blob_storage.py:
237-248, 991-1009, 1304-1308, 1376-1383, 1662-1680,
1935-2008, 2026-2052, 2066-2075, 2108-2143, 2157-2169.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from app.modules.transformers.blob_storage import BlobStorage, _add_custom_metadata_to_form


def _make_blob_storage(config_service=None, graph_provider=None, logger=None):
    logger = logger or MagicMock()
    config_service = config_service or AsyncMock()
    graph_provider = graph_provider or AsyncMock()
    return BlobStorage(logger=logger, config_service=config_service, graph_provider=graph_provider)


MODULE = "app.modules.transformers.blob_storage"


def _resp(status=200, json_value=None, text_value=""):
    r = AsyncMock()
    r.status = status
    r.json = AsyncMock(return_value=json_value if json_value is not None else {})
    r.text = AsyncMock(return_value=text_value)
    r.content_type = "application/json"
    r.__aenter__ = AsyncMock(return_value=r)
    r.__aexit__ = AsyncMock(return_value=False)
    return r


def _session(post_resp=None, get_resp=None):
    s = AsyncMock()
    if post_resp is not None:
        s.post = MagicMock(return_value=post_resp)
    if get_resp is not None:
        s.get = MagicMock(return_value=get_resp)
    s.__aenter__ = AsyncMock(return_value=s)
    s.__aexit__ = AsyncMock(return_value=False)
    return s


# ============================================================================
# _add_custom_metadata_to_form (lines 237-248)
# ============================================================================


class TestAddCustomMetadataToForm:
    def test_bool_value_lowered(self):
        form = MagicMock(spec=aiohttp.FormData)
        _add_custom_metadata_to_form(form, [{"key": "compressed", "value": True}])
        form.add_field.assert_any_call("customMetadata[0][key]", "compressed")
        form.add_field.assert_any_call("customMetadata[0][value]", "true")

    def test_bool_false_lowered(self):
        form = MagicMock(spec=aiohttp.FormData)
        _add_custom_metadata_to_form(form, [{"key": "flag", "value": False}])
        form.add_field.assert_any_call("customMetadata[0][value]", "false")

    def test_string_value_passed_through(self):
        form = MagicMock(spec=aiohttp.FormData)
        _add_custom_metadata_to_form(form, [{"key": "algo", "value": "zstd"}])
        form.add_field.assert_any_call("customMetadata[0][value]", "zstd")

    def test_int_value_stringified(self):
        form = MagicMock(spec=aiohttp.FormData)
        _add_custom_metadata_to_form(form, [{"key": "level", "value": 10}])
        form.add_field.assert_any_call("customMetadata[0][value]", "10")

    def test_multiple_entries(self):
        form = MagicMock(spec=aiohttp.FormData)
        entries = [
            {"key": "a", "value": True},
            {"key": "b", "value": "hello"},
            {"key": "c", "value": 42},
        ]
        _add_custom_metadata_to_form(form, entries)
        form.add_field.assert_any_call("customMetadata[0][key]", "a")
        form.add_field.assert_any_call("customMetadata[1][key]", "b")
        form.add_field.assert_any_call("customMetadata[2][key]", "c")


# ============================================================================
# save_record_to_storage with compression (lines 991-1009)
# ============================================================================


class TestSaveRecordToStorageCompression:
    @pytest.mark.asyncio
    async def test_local_storage_with_compression_metadata(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "local")
        )
        bs._maybe_compress_record = MagicMock(return_value=("compressed_b64", True))

        post_resp = _resp(200, {"_id": "doc-123"})
        mock_session = _session(post_resp=post_resp)

        with patch(f"{MODULE}.aiohttp.ClientSession", return_value=mock_session):
            doc_id, size = await bs.save_record_to_storage(
                "org-1", "rec-1", "vr-1", {"key": "val"}
            )

        assert doc_id == "doc-123"
        assert size is not None
        mock_session.post.assert_called_once()


# ============================================================================
# batch_lookup: PIPESHUB_VRID_FIELD_LOOKUP env var branch (lines 1304-1308)
# ============================================================================


class TestBatchLookupVridFieldFallback:
    @pytest.mark.asyncio
    async def test_field_lookup_exception_logged_and_continues(self):
        graph = AsyncMock()
        graph.get_nodes_by_field_in = AsyncMock(
            side_effect=[
                [{"id": "vr-1", "record_doc_id": "d1"}],
                RuntimeError("field lookup failed"),
            ]
        )
        graph.get_document = AsyncMock(return_value={"record_doc_id": "d2"})
        bs = _make_blob_storage(graph_provider=graph)

        with patch.dict(os.environ, {"PIPESHUB_VRID_FIELD_LOOKUP": "true"}):
            result = await bs.get_document_ids_by_virtual_record_ids(["vr-1", "vr-2"])

        assert "vr-1" in result
        bs.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_field_lookup_success_resolves_nodes(self):
        graph = AsyncMock()
        graph.get_nodes_by_field_in = AsyncMock(
            side_effect=[
                [],
                [{"virtualRecordId": "vr-1", "record_doc_id": "d1"}],
            ]
        )
        graph.get_document = AsyncMock(return_value=None)
        bs = _make_blob_storage(graph_provider=graph)

        with patch.dict(os.environ, {"PIPESHUB_VRID_FIELD_LOOKUP": "1"}):
            result = await bs.get_document_ids_by_virtual_record_ids(["vr-1"])

        assert "vr-1" in result
        assert result["vr-1"]["record_doc_id"] == "d1"


# ============================================================================
# get_record_from_storage: cached signed URL fails (lines 1376-1383)
# ============================================================================


class TestDownloadRecordCachedUrlFallback:
    @pytest.mark.asyncio
    async def test_cached_url_exception_falls_through_to_gateway(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "local")
        )
        bs._cached_signed_url = AsyncMock(return_value="https://s3.example.com/cached")
        bs._record_from_signed_url = AsyncMock(side_effect=RuntimeError("expired"))

        get_resp = _resp(200, {"record": {"blocks": []}})
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=get_resp)

        bs._process_downloaded_record = MagicMock(return_value={"record_name": "r1", "blocks": []})

        lookup = {"record_doc_id": "doc-1", "fileSizeBytes": 100}

        with patch(f"{MODULE}.get_shared_session", return_value=mock_session):
            result = await bs.get_record_from_storage("vr-1", "org-1", lookup_result=lookup)

        assert result is not None
        bs.logger.debug.assert_called()
        bs._record_from_signed_url.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cached_url_returns_none_falls_through(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "local")
        )
        bs._cached_signed_url = AsyncMock(return_value="https://s3.example.com/cached")
        bs._record_from_signed_url = AsyncMock(return_value=None)

        get_resp = _resp(200, {"record": {"blocks": []}})
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=get_resp)

        bs._process_downloaded_record = MagicMock(return_value={"record_name": "r1"})

        lookup = {"record_doc_id": "doc-1", "fileSizeBytes": 100}

        with patch(f"{MODULE}.get_shared_session", return_value=mock_session):
            result = await bs.get_record_from_storage("vr-1", "org-1", lookup_result=lookup)

        assert result is not None


# ============================================================================
# _create_metadata_document with compression (lines 1662-1680)
# ============================================================================


class TestCreateMetadataDocumentCompression:
    @pytest.mark.asyncio
    async def test_local_storage_with_compression(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "local")
        )
        bs._maybe_compress_record = MagicMock(return_value=("compressed_b64", True))

        post_resp = _resp(200, {"_id": "meta-doc-1"})
        mock_session = _session(post_resp=post_resp)

        with patch(f"{MODULE}.aiohttp.ClientSession", return_value=mock_session):
            doc_id = await bs._create_metadata_document(
                "org-1", "rec-1", "vr-1", {"hash_to_block_ids": {}}
            )

        assert doc_id == "meta-doc-1"
        mock_session.post.assert_called_once()


# ============================================================================
# save_versioned_artifact_to_storage (lines 1935-2008)
# ============================================================================


class TestSaveVersionedArtifactToStorage:
    @pytest.mark.asyncio
    async def test_local_storage_success(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "local")
        )
        bs._get_public_download_base_url = AsyncMock(return_value="http://pub:3000")

        post_resp = _resp(200, {"_id": "art-doc-1"})
        mock_session = _session(post_resp=post_resp)

        with patch(f"{MODULE}.aiohttp.ClientSession", return_value=mock_session):
            result = await bs.save_versioned_artifact_to_storage(
                "org-1", "conv-1", "artifact.html", b"<h1>Hello</h1>", "text/html"
            )

        assert result["documentId"] == "art-doc-1"
        assert "downloadUrl" in result
        assert result["fileName"] == "artifact.html"

    @pytest.mark.asyncio
    async def test_local_storage_upload_fails(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "local")
        )

        post_resp = _resp(500, text_value="Internal error")
        mock_session = _session(post_resp=post_resp)

        with patch(f"{MODULE}.aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(Exception, match="Local upload failed"):
                await bs.save_versioned_artifact_to_storage(
                    "org-1", "conv-1", "artifact.html", b"<h1>Hello</h1>"
                )

    @pytest.mark.asyncio
    async def test_cloud_storage_with_signed_url(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "s3")
        )
        bs._create_placeholder = AsyncMock(return_value={"_id": "art-doc-2"})
        bs._get_signed_url = AsyncMock(return_value={"signedUrl": "https://s3/put-url"})
        bs._upload_raw_to_signed_url = AsyncMock()

        get_resp = _resp(200, {"signedUrl": "https://s3/get-url"})
        mock_session = _session(get_resp=get_resp)

        with patch(f"{MODULE}.aiohttp.ClientSession", return_value=mock_session):
            result = await bs.save_versioned_artifact_to_storage(
                "org-1", "conv-1", "report.pdf", b"%PDF", "application/pdf"
            )

        assert result["documentId"] == "art-doc-2"
        assert result["signedUrl"] == "https://s3/get-url"

    @pytest.mark.asyncio
    async def test_cloud_storage_fallback_to_download_url(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "s3")
        )
        bs._create_placeholder = AsyncMock(return_value={"_id": "art-doc-3"})
        bs._get_signed_url = AsyncMock(return_value={"signedUrl": "https://s3/put-url"})
        bs._upload_raw_to_signed_url = AsyncMock()
        bs._get_public_download_base_url = AsyncMock(return_value="http://pub:3000")

        get_resp = _resp(200, {})
        mock_session = _session(get_resp=get_resp)

        with patch(f"{MODULE}.aiohttp.ClientSession", return_value=mock_session):
            result = await bs.save_versioned_artifact_to_storage(
                "org-1", "conv-1", "report.pdf", b"%PDF"
            )

        assert result["documentId"] == "art-doc-3"
        assert "downloadUrl" in result

    @pytest.mark.asyncio
    async def test_cloud_storage_no_placeholder_id_raises(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "s3")
        )
        bs._create_placeholder = AsyncMock(return_value={})

        mock_session = _session()

        with patch(f"{MODULE}.aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(Exception, match="No document ID in placeholder"):
                await bs.save_versioned_artifact_to_storage(
                    "org-1", "conv-1", "f.txt", b"data"
                )


# ============================================================================
# get_download_url (lines 2026-2052)
# ============================================================================


class TestGetDownloadUrl:
    @pytest.mark.asyncio
    async def test_cloud_with_signed_url(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "s3")
        )

        get_resp = _resp(200, {"signedUrl": "https://s3/signed"})
        mock_session = _session(get_resp=get_resp)

        with patch(f"{MODULE}.aiohttp.ClientSession", return_value=mock_session):
            url = await bs.get_download_url("org-1", "doc-1")

        assert url == "https://s3/signed"

    @pytest.mark.asyncio
    async def test_cloud_no_signed_url_falls_through(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "s3")
        )
        bs._get_public_download_base_url = AsyncMock(return_value="http://pub:3000")

        get_resp = _resp(200, {})
        mock_session = _session(get_resp=get_resp)

        with patch(f"{MODULE}.aiohttp.ClientSession", return_value=mock_session):
            url = await bs.get_download_url("org-1", "doc-1")

        assert "doc-1" in url
        assert url.startswith("http://pub:3000")

    @pytest.mark.asyncio
    async def test_local_storage_goes_to_external_url(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "local")
        )
        bs._get_public_download_base_url = AsyncMock(return_value="http://pub:3000")

        url = await bs.get_download_url("org-1", "doc-1")

        assert "doc-1" in url
        assert url.startswith("http://pub:3000")

    @pytest.mark.asyncio
    async def test_version_appended_as_query_param(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "local")
        )
        bs._get_public_download_base_url = AsyncMock(return_value="http://pub:3000")

        url = await bs.get_download_url("org-1", "doc-1", version=3)

        assert "?version=3" in url


# ============================================================================
# get_direct_upload_url (lines 2066-2075)
# ============================================================================


class TestGetDirectUploadUrl:
    @pytest.mark.asyncio
    async def test_local_storage_raises(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "local")
        )

        with pytest.raises(Exception, match="not supported for local storage"):
            await bs.get_direct_upload_url("org-1", "doc-1")

    @pytest.mark.asyncio
    async def test_cloud_success(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "s3")
        )
        bs._get_signed_url = AsyncMock(return_value={"signedUrl": "https://s3/put-url"})

        mock_session = _session()

        with patch(f"{MODULE}.aiohttp.ClientSession", return_value=mock_session):
            url = await bs.get_direct_upload_url("org-1", "doc-1")

        assert url == "https://s3/put-url"

    @pytest.mark.asyncio
    async def test_cloud_no_signed_url_raises(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "s3")
        )
        bs._get_signed_url = AsyncMock(return_value={})

        mock_session = _session()

        with patch(f"{MODULE}.aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(Exception, match="No signed URL returned"):
                await bs.get_direct_upload_url("org-1", "doc-1")


# ============================================================================
# upload_artifact_version (lines 2108-2143)
# ============================================================================


class TestUploadArtifactVersion:
    @pytest.mark.asyncio
    async def test_success_with_version_history(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "s3")
        )

        post_resp = _resp(200, {
            "versionHistory": [{"version": 0}, {"version": 1}]
        })
        mock_session = _session(post_resp=post_resp)

        with patch(f"{MODULE}.aiohttp.ClientSession", return_value=mock_session):
            result = await bs.upload_artifact_version(
                "org-1", "doc-1", "v2.html", b"<h1>v2</h1>", "text/html"
            )

        assert result["documentId"] == "doc-1"
        assert result["storageVersion"] == 1
        assert result["priorStorageVersion"] == 0
        assert result["sizeBytes"] == len(b"<h1>v2</h1>")

    @pytest.mark.asyncio
    async def test_upload_failure_raises(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "s3")
        )

        post_resp = _resp(500, text_value="Server error")
        mock_session = _session(post_resp=post_resp)

        with patch(f"{MODULE}.aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(Exception, match="Failed to upload artifact version"):
                await bs.upload_artifact_version(
                    "org-1", "doc-1", "v2.html", b"<h1>v2</h1>"
                )

    @pytest.mark.asyncio
    async def test_cannot_be_versioned_error(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "s3")
        )

        post_resp = _resp(400, text_value="This document cannot be versioned because it lacks isVersionedFile")
        mock_session = _session(post_resp=post_resp)

        with patch(f"{MODULE}.aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(Exception, match="cannot be versioned"):
                await bs.upload_artifact_version(
                    "org-1", "doc-1", "v2.html", b"<h1>v2</h1>"
                )

    @pytest.mark.asyncio
    async def test_content_type_error_on_response(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "s3")
        )

        post_resp = AsyncMock()
        post_resp.status = 200
        post_resp.json = AsyncMock(side_effect=aiohttp.ContentTypeError(
            MagicMock(), MagicMock()
        ))
        post_resp.text = AsyncMock(return_value="")
        post_resp.__aenter__ = AsyncMock(return_value=post_resp)
        post_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = _session(post_resp=post_resp)

        with patch(f"{MODULE}.aiohttp.ClientSession", return_value=mock_session):
            result = await bs.upload_artifact_version(
                "org-1", "doc-1", "v2.html", b"<h1>v2</h1>"
            )

        assert result["storageVersion"] is None
        assert result["priorStorageVersion"] is None

    @pytest.mark.asyncio
    async def test_empty_version_history(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "s3")
        )

        post_resp = _resp(200, {"versionHistory": []})
        mock_session = _session(post_resp=post_resp)

        with patch(f"{MODULE}.aiohttp.ClientSession", return_value=mock_session):
            result = await bs.upload_artifact_version(
                "org-1", "doc-1", "v2.html", b"data"
            )

        assert result["storageVersion"] is None
        assert result["priorStorageVersion"] is None
        bs.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_single_version_history_entry(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "s3")
        )

        post_resp = _resp(200, {"versionHistory": [{"version": 0}]})
        mock_session = _session(post_resp=post_resp)

        with patch(f"{MODULE}.aiohttp.ClientSession", return_value=mock_session):
            result = await bs.upload_artifact_version(
                "org-1", "doc-1", "v2.html", b"data"
            )

        assert result["storageVersion"] == 0
        assert result["priorStorageVersion"] is None


# ============================================================================
# get_document_version_history (lines 2157-2169)
# ============================================================================


class TestGetDocumentVersionHistory:
    @pytest.mark.asyncio
    async def test_success(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "s3")
        )

        version_data = [{"version": 0}, {"version": 1}]
        get_resp = _resp(200, {"versionHistory": version_data})
        mock_session = _session(get_resp=get_resp)

        with patch(f"{MODULE}.aiohttp.ClientSession", return_value=mock_session):
            result = await bs.get_document_version_history("org-1", "doc-1")

        assert result == version_data

    @pytest.mark.asyncio
    async def test_no_version_history_returns_empty(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "s3")
        )

        get_resp = _resp(200, {})
        mock_session = _session(get_resp=get_resp)

        with patch(f"{MODULE}.aiohttp.ClientSession", return_value=mock_session):
            result = await bs.get_document_version_history("org-1", "doc-1")

        assert result == []

    @pytest.mark.asyncio
    async def test_failure_raises(self):
        bs = _make_blob_storage()
        bs._get_auth_and_config = AsyncMock(
            return_value=({"Authorization": "Bearer tok"}, "http://node:3000", "s3")
        )

        get_resp = _resp(404, text_value="Not found")
        mock_session = _session(get_resp=get_resp)

        with patch(f"{MODULE}.aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(Exception, match="Failed to fetch document doc-1"):
                await bs.get_document_version_history("org-1", "doc-1")
