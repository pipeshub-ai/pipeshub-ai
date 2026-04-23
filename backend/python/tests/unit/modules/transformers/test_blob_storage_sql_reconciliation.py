"""Tests for BlobStorage upload_next_version, save_reconciliation_metadata,
_create_metadata_document, and get_reconciliation_metadata paths."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.transformers.blob_storage import BlobStorage


def _make_bs(*, config_service=None, graph_provider=None):
    return BlobStorage(
        logger=MagicMock(),
        config_service=config_service or AsyncMock(),
        graph_provider=graph_provider or AsyncMock(),
    )


def _configure_auth(bs, storage_type="s3"):
    bs.config_service.get_config = AsyncMock(
        side_effect=[
            {"scopedJwtSecret": "secret"},
            {"cm": {"endpoint": "http://localhost:3001"}},
            {"storageType": storage_type},
        ]
    )


def _resp(status=200, json_value=None):
    r = AsyncMock()
    r.status = status
    r.json = AsyncMock(return_value=json_value or {})
    r.__aenter__ = AsyncMock(return_value=r)
    r.__aexit__ = AsyncMock(return_value=False)
    return r


# ---------------------------------------------------------------------------
# upload_next_version
# ---------------------------------------------------------------------------


class TestUploadNextVersionLocal:
    @pytest.mark.asyncio
    async def test_local_success(self):
        bs = _make_bs()
        _configure_auth(bs, "local")

        upload_resp = _resp(200, {"_id": "doc-new"})

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=upload_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            doc_id, size = await bs.upload_next_version(
                "org-1", "rec-1", "doc-123",
                {"data": "hello"}, "vr-1",
            )
        assert doc_id == "doc-123"
        assert size > 0

    @pytest.mark.asyncio
    async def test_local_non_200_raises(self):
        bs = _make_bs()
        _configure_auth(bs, "local")

        err_resp = _resp(500, {"error": "boom"})

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=err_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            with pytest.raises(Exception, match="Failed to upload next version"):
                await bs.upload_next_version(
                    "org-1", "rec-1", "doc-123",
                    {"data": "hello"}, "vr-1",
                )

    @pytest.mark.asyncio
    async def test_local_non_versioned_document_raises_specific_error(self):
        bs = _make_bs()
        _configure_auth(bs, "local")

        err_resp = _resp(
            400,
            {
                "error": {
                    "code": "HTTP_BAD_REQUEST",
                    "message": "This document cannot be versioned",
                }
            },
        )

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=err_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            with pytest.raises(Exception, match="This document cannot be versioned"):
                await bs.upload_next_version(
                    "org-1", "rec-1", "doc-legacy",
                    {"data": "hello"}, "vr-1",
                )


class TestUploadNextVersionS3:
    @pytest.mark.asyncio
    async def test_s3_success(self):
        bs = _make_bs()
        _configure_auth(bs, "s3")
        bs._get_signed_url = AsyncMock(return_value={"signedUrl": "https://x/y"})
        bs._upload_to_signed_url = AsyncMock(return_value=200)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            doc_id, size = await bs.upload_next_version(
                "org-1", "rec-1", "doc-X",
                {"r": 1}, "vr-1",
            )
        assert doc_id == "doc-X"
        assert size > 0
        bs._upload_to_signed_url.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_s3_missing_signed_url_raises(self):
        bs = _make_bs()
        _configure_auth(bs, "s3")
        bs._get_signed_url = AsyncMock(return_value={})  # no signedUrl

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            with pytest.raises(Exception, match="No signed URL"):
                await bs.upload_next_version(
                    "org-1", "rec-1", "doc-X", {"r": 1}, "vr-1",
                )

    @pytest.mark.asyncio
    async def test_compression_failure_falls_back_to_uncompressed(self):
        bs = _make_bs()
        _configure_auth(bs, "s3")
        bs._get_signed_url = AsyncMock(return_value={"signedUrl": "https://x/y"})
        bs._upload_to_signed_url = AsyncMock(return_value=200)
        bs._compress_record = MagicMock(side_effect=RuntimeError("compress boom"))

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            doc_id, size = await bs.upload_next_version(
                "org-1", "rec-1", "doc-Y", {"r": 1}, "vr-1",
            )
        assert doc_id == "doc-Y"
        assert size > 0


# ---------------------------------------------------------------------------
# save_reconciliation_metadata
# ---------------------------------------------------------------------------


class TestSaveReconciliationMetadata:
    @pytest.mark.asyncio
    async def test_no_existing_mapping_creates_new_document(self):
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value=None)
        graph_provider.batch_upsert_nodes = AsyncMock()
        bs = _make_bs(graph_provider=graph_provider)
        bs._create_metadata_document = AsyncMock(return_value="meta-new")

        result = await bs.save_reconciliation_metadata(
            "org", "rec", "vr", {"a": 1},
        )

        assert result == "meta-new"
        bs._create_metadata_document.assert_awaited_once()
        graph_provider.batch_upsert_nodes.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_existing_mapping_uploads_next_version(self):
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"record_metadata_doc_id": "existing"}
        )
        graph_provider.batch_upsert_nodes = AsyncMock()
        bs = _make_bs(graph_provider=graph_provider)
        bs.upload_next_version = AsyncMock(return_value=("existing", 123))

        result = await bs.save_reconciliation_metadata(
            "org", "rec", "vr", {"a": 1},
        )
        assert result == "existing"
        bs.upload_next_version.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lookup_error_falls_back_to_create(self):
        """If graph lookup fails, creation path is still taken."""
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(side_effect=RuntimeError("db err"))
        graph_provider.batch_upsert_nodes = AsyncMock()
        bs = _make_bs(graph_provider=graph_provider)
        bs._create_metadata_document = AsyncMock(return_value="fresh")

        result = await bs.save_reconciliation_metadata(
            "org", "rec", "vr", {},
        )
        assert result == "fresh"

    @pytest.mark.asyncio
    async def test_failure_in_create_propagates(self):
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value=None)
        bs = _make_bs(graph_provider=graph_provider)
        bs._create_metadata_document = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError, match="boom"):
            await bs.save_reconciliation_metadata("org", "rec", "vr", {})

    @pytest.mark.asyncio
    async def test_existing_non_versioned_metadata_falls_back_to_create(self):
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"record_metadata_doc_id": "legacy-meta"}
        )
        graph_provider.batch_upsert_nodes = AsyncMock()
        bs = _make_bs(graph_provider=graph_provider)
        bs.upload_next_version = AsyncMock(
            side_effect=Exception("This document cannot be versioned")
        )
        bs._create_metadata_document = AsyncMock(return_value="meta-new")

        result = await bs.save_reconciliation_metadata(
            "org", "rec", "vr", {"k": "v"},
        )

        assert result == "meta-new"
        bs._create_metadata_document.assert_awaited_once()
        graph_provider.batch_upsert_nodes.assert_awaited_once()


# ---------------------------------------------------------------------------
# _create_metadata_document
# ---------------------------------------------------------------------------


class TestCreateMetadataDocumentLocal:
    @pytest.mark.asyncio
    async def test_local_success(self):
        bs = _make_bs()
        _configure_auth(bs, "local")

        post_resp = _resp(200, {"_id": "meta-local"})
        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=post_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            result = await bs._create_metadata_document(
                "org", "rec", "vr", {"m": 1},
            )
        assert result == "meta-local"

    @pytest.mark.asyncio
    async def test_local_non_200_raises(self):
        bs = _make_bs()
        _configure_auth(bs, "local")

        err_resp = _resp(500, {"error": "kaboom"})
        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=err_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            with pytest.raises(Exception, match="Failed to create metadata document"):
                await bs._create_metadata_document("org", "rec", "vr", {})

    @pytest.mark.asyncio
    async def test_local_missing_document_id_raises(self):
        bs = _make_bs()
        _configure_auth(bs, "local")

        post_resp = _resp(200, {})  # no _id
        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=post_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            with pytest.raises(Exception, match="No document ID"):
                await bs._create_metadata_document("org", "rec", "vr", {})


class TestCreateMetadataDocumentS3:
    @pytest.mark.asyncio
    async def test_s3_success(self):
        bs = _make_bs()
        _configure_auth(bs, "s3")
        bs._create_placeholder = AsyncMock(return_value={"_id": "meta-s3"})
        bs._get_signed_url = AsyncMock(return_value={"signedUrl": "https://x/y"})
        bs._upload_to_signed_url = AsyncMock(return_value=200)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            result = await bs._create_metadata_document(
                "org", "rec", "vr", {"a": 1},
            )
        assert result == "meta-s3"

    @pytest.mark.asyncio
    async def test_s3_missing_placeholder_id_raises(self):
        bs = _make_bs()
        _configure_auth(bs, "s3")
        bs._create_placeholder = AsyncMock(return_value={})  # missing _id

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            with pytest.raises(Exception, match="No document ID"):
                await bs._create_metadata_document("org", "rec", "vr", {})

    @pytest.mark.asyncio
    async def test_s3_missing_signed_url_raises(self):
        bs = _make_bs()
        _configure_auth(bs, "s3")
        bs._create_placeholder = AsyncMock(return_value={"_id": "meta"})
        bs._get_signed_url = AsyncMock(return_value={})

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            with pytest.raises(Exception, match="No signed URL"):
                await bs._create_metadata_document("org", "rec", "vr", {})

    @pytest.mark.asyncio
    async def test_s3_compression_failure_uses_uncompressed(self):
        bs = _make_bs()
        _configure_auth(bs, "s3")
        bs._create_placeholder = AsyncMock(return_value={"_id": "meta"})
        bs._get_signed_url = AsyncMock(return_value={"signedUrl": "https://x"})
        bs._upload_to_signed_url = AsyncMock(return_value=200)
        bs._compress_record = MagicMock(side_effect=RuntimeError("fail"))

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            result = await bs._create_metadata_document("org", "rec", "vr", {"a": 1})
        assert result == "meta"


# ---------------------------------------------------------------------------
# get_reconciliation_metadata
# ---------------------------------------------------------------------------


class TestGetReconciliationMetadata:
    @pytest.mark.asyncio
    async def test_no_graph_provider_returns_none(self):
        bs = BlobStorage(logger=MagicMock(), config_service=AsyncMock(), graph_provider=None)
        result = await bs.get_reconciliation_metadata("vr", "org")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_mapping_returns_none(self):
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value=None)
        bs = _make_bs(graph_provider=graph_provider)
        result = await bs.get_reconciliation_metadata("vr", "org")
        assert result is None

    @pytest.mark.asyncio
    async def test_mapping_without_metadata_doc_id_returns_none(self):
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"other": "x"})
        bs = _make_bs(graph_provider=graph_provider)
        result = await bs.get_reconciliation_metadata("vr", "org")
        assert result is None

    @pytest.mark.asyncio
    async def test_lookup_error_returns_none(self):
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(side_effect=RuntimeError("boom"))
        bs = _make_bs(graph_provider=graph_provider)
        result = await bs.get_reconciliation_metadata("vr", "org")
        assert result is None

    @pytest.mark.asyncio
    async def test_download_uncompressed_record_wrapped(self):
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"record_metadata_doc_id": "meta-1"}
        )
        bs = _make_bs(graph_provider=graph_provider)
        _configure_auth(bs, "s3")

        payload = {"record": {"hash_to_block_ids": {"h": ["b"]}}}
        download_resp = _resp(200, payload)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=download_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            result = await bs.get_reconciliation_metadata("vr", "org")
        assert result == payload["record"]

    @pytest.mark.asyncio
    async def test_download_failure_returns_none(self):
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"record_metadata_doc_id": "meta-1"}
        )
        bs = _make_bs(graph_provider=graph_provider)
        _configure_auth(bs, "s3")

        err_resp = _resp(404, {})
        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=err_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            result = await bs.get_reconciliation_metadata("vr", "org")
        assert result is None

    @pytest.mark.asyncio
    async def test_download_with_compressed_record(self):
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"record_metadata_doc_id": "meta-1"}
        )
        bs = _make_bs(graph_provider=graph_provider)
        _configure_auth(bs, "s3")

        payload = {"isCompressed": True, "record": "dummy"}
        download_resp = _resp(200, payload)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=download_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        bs._process_downloaded_record = MagicMock(return_value={"decoded": "yes"})

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            result = await bs.get_reconciliation_metadata("vr", "org")
        assert result == {"decoded": "yes"}

    @pytest.mark.asyncio
    async def test_auth_failure_during_download_returns_none(self):
        """An exception in _get_auth_and_config should be caught and None returned."""
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"record_metadata_doc_id": "meta-1"}
        )
        bs = _make_bs(graph_provider=graph_provider)
        bs._get_auth_and_config = AsyncMock(side_effect=RuntimeError("auth fail"))

        result = await bs.get_reconciliation_metadata("vr", "org")
        assert result is None
