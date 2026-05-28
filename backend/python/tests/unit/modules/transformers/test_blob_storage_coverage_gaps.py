"""Targeted tests to raise coverage for blob_storage.py remaining branches."""

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import DefaultEndpoints
from app.modules.transformers.blob_storage import BlobStorage


def _make_bs(*, config_service=None, graph_provider=None):
    return BlobStorage(
        logger=MagicMock(),
        config_service=config_service or AsyncMock(),
        graph_provider=graph_provider,
    )


def _auth_side_effect(storage_type: str):
    return [
        {"scopedJwtSecret": "scoped-jwt-secret-32bytes-min"},
        {"cm": {"endpoint": "http://nodejs:3001"}},
        {"storageType": storage_type},
    ]


def _resp(status=200, json_value=None, text_value=""):
    r = AsyncMock()
    r.status = status
    r.json = AsyncMock(return_value=json_value if json_value is not None else {})
    r.text = AsyncMock(return_value=text_value)
    r.__aenter__ = AsyncMock(return_value=r)
    r.__aexit__ = AsyncMock(return_value=False)
    return r


def _make_record_mock(org_id="org-1", record_id="rec-1", virtual_record_id="vr-1"):
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


# ---------------------------------------------------------------------------
# _get_public_download_base_url
# ---------------------------------------------------------------------------


class TestGetPublicDownloadBaseUrl:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "endpoints_payload, expected",
        [
            (
                {"frontend": {"publicEndpoint": "https://app.example"}},
                "https://app.example",
            ),
            (
                {"frontend": {}, "storage": {"endpoint": "https://storage.example"}},
                "https://storage.example",
            ),
            (
                {},
                DefaultEndpoints.FRONTEND_ENDPOINT.value,
            ),
        ],
    )
    async def test_fallback_chain(self, endpoints_payload, expected):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=endpoints_payload)
        bs = _make_bs(config_service=cs)
        url = await bs._get_public_download_base_url()
        assert url == expected


# ---------------------------------------------------------------------------
# save_binary_to_storage
# ---------------------------------------------------------------------------


class TestSaveBinaryToStorage:
    @pytest.mark.asyncio
    async def test_local_success(self):
        bs = _make_bs()
        bs.config_service.get_config = AsyncMock(side_effect=_auth_side_effect("local"))

        post_resp = _resp(200, {"_id": "bin-local"})
        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=post_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            doc_id, size = await bs.save_binary_to_storage(
                "org-1",
                "rec-1",
                "report.pdf",
                "pdf",
                "application/pdf",
                b"%PDF-1.4\n",
            )
        assert doc_id == "bin-local"
        assert size == len(b"%PDF-1.4\n")

    @pytest.mark.asyncio
    async def test_local_non_success_returns_none(self):
        bs = _make_bs()
        bs.config_service.get_config = AsyncMock(side_effect=_auth_side_effect("local"))

        post_resp = _resp(500, text_value="error")
        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=post_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            doc_id, size = await bs.save_binary_to_storage(
                "org-1", "rec-1", "f.pdf", "pdf", "application/pdf", b"x",
            )
        assert doc_id is None
        assert size is None

    @pytest.mark.asyncio
    async def test_s3_success(self):
        bs = _make_bs()
        bs.config_service.get_config = AsyncMock(side_effect=_auth_side_effect("s3"))

        placeholder_r = _resp(200, {"_id": "s3-bin"})
        signed_post_r = _resp(200, {"signedUrl": "https://bucket/presigned"})
        put_r = _resp(HttpStatusCode.SUCCESS.value, {})

        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=[placeholder_r, signed_post_r])
        mock_session.put = MagicMock(return_value=put_r)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            doc_id, size = await bs.save_binary_to_storage(
                "org-1", "rec-1", "x.csv", "csv", "text/csv", b"a,b\n",
            )
        assert doc_id == "s3-bin"
        assert size == len(b"a,b\n")

    @pytest.mark.asyncio
    async def test_s3_no_placeholder_id_returns_none(self):
        bs = _make_bs()
        bs.config_service.get_config = AsyncMock(side_effect=_auth_side_effect("s3"))

        placeholder_r = _resp(200, {})
        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=placeholder_r)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            doc_id, size = await bs.save_binary_to_storage(
                "org-1", "rec-1", "f.bin", "bin", "application/octet-stream", b"x",
            )
        assert doc_id is None
        assert size is None

    @pytest.mark.asyncio
    async def test_s3_no_signed_url_returns_none(self):
        bs = _make_bs()
        bs.config_service.get_config = AsyncMock(side_effect=_auth_side_effect("s3"))

        placeholder_r = _resp(200, {"_id": "doc1"})
        signed_post_r = _resp(200, {})
        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=[placeholder_r, signed_post_r])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            doc_id, size = await bs.save_binary_to_storage(
                "org-1", "rec-1", "f.bin", "bin", "application/octet-stream", b"x",
            )
        assert doc_id is None
        assert size is None

    @pytest.mark.asyncio
    async def test_outer_exception_returns_none(self):
        bs = _make_bs()
        bs.config_service.get_config = AsyncMock(side_effect=ValueError("config broken"))

        doc_id, size = await bs.save_binary_to_storage(
            "org-1", "rec-1", "f.bin", "bin", "application/octet-stream", b"x",
        )
        assert doc_id is None
        assert size is None


# ---------------------------------------------------------------------------
# apply — upload_next_version raises non–versioning errors
# ---------------------------------------------------------------------------


class TestApplyUploadNextVersionErrors:
    @pytest.mark.asyncio
    async def test_propagates_when_not_versioning_error(self):
        gp = AsyncMock()
        bs = _make_bs(graph_provider=gp)
        bs.get_document_id_by_virtual_record_id = AsyncMock(
            return_value={"record_doc_id": "doc-existing"}
        )
        bs.upload_next_version = AsyncMock(side_effect=RuntimeError("upstream failure"))
        bs.save_record_to_storage = AsyncMock()

        ctx = MagicMock()
        ctx.record = _make_record_mock()

        with pytest.raises(RuntimeError, match="upstream failure"):
            await bs.apply(ctx)

        bs.save_record_to_storage.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_versioned_exception_message_detected(self):
        """_is_non_versioned_exception is case-insensitive on the phrase."""
        bs = _make_bs()
        assert bs._is_non_versioned_exception(Exception("Cannot Be Versioned")) is True
        assert bs._is_non_versioned_exception(Exception("other")) is False


# ---------------------------------------------------------------------------
# get_document_id_by_virtual_record_id — record_metadata_doc_id
# ---------------------------------------------------------------------------


class TestGetDocumentIdMetadataField:
    @pytest.mark.asyncio
    async def test_includes_record_metadata_doc_id_when_present(self):
        gp = AsyncMock()
        gp.get_nodes_by_filters = AsyncMock(
            return_value=[
                {
                    "record_doc_id": "rd1",
                    "fileSizeBytes": 10,
                    "record_metadata_doc_id": "meta-z",
                }
            ]
        )
        bs = _make_bs(graph_provider=gp)
        result = await bs.get_document_id_by_virtual_record_id("vr-99")
        assert result["record_doc_id"] == "rd1"
        assert result["record_metadata_doc_id"] == "meta-z"


# ---------------------------------------------------------------------------
# get_record_from_storage — no document id after lookup
# ---------------------------------------------------------------------------


class TestGetRecordFromStorageNoDocId:
    @pytest.mark.asyncio
    async def test_returns_none_when_only_size_known(self):
        bs = _make_bs()
        bs.config_service.get_config = AsyncMock(side_effect=_auth_side_effect("s3"))
        bs.get_document_id_by_virtual_record_id = AsyncMock(
            return_value={"fileSizeBytes": 100, "record_doc_id": None},
        )

        out = await bs.get_record_from_storage("vr-x", "org-1")
        assert out is None


# ---------------------------------------------------------------------------
# get_reconciliation_metadata — signed URL follow-up + raw dict body
# ---------------------------------------------------------------------------


class TestGetReconciliationMetadataDeep:
    @pytest.mark.asyncio
    async def test_signed_url_secondary_fetch_returns_plain_dict(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(
            return_value={"record_metadata_doc_id": "meta-doc"},
        )
        bs = _make_bs(graph_provider=gp)
        bs.config_service.get_config = AsyncMock(side_effect=_auth_side_effect("s3"))

        dl = _resp(200, {"signedUrl": "https://cdn.example/object"})
        nested = _resp(200, {"hash": "only-top-level", "no_record_key": True})

        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=[dl, nested])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            result = await bs.get_reconciliation_metadata("vr-1", "org-1")

        assert result == {"hash": "only-top-level", "no_record_key": True}


# ---------------------------------------------------------------------------
# save_reconciliation_metadata — no graph provider skips mapping upsert
# ---------------------------------------------------------------------------


class TestSaveReconciliationMetadataNoGraph:
    @pytest.mark.asyncio
    async def test_returns_doc_id_without_upsert_when_no_graph(self):
        bs = BlobStorage(
            logger=MagicMock(),
            config_service=AsyncMock(),
            graph_provider=None,
        )
        bs._create_metadata_document = AsyncMock(return_value="meta-standalone")

        out = await bs.save_reconciliation_metadata("o1", "r1", "vr1", {"k": 1})

        assert out == "meta-standalone"
        bs._create_metadata_document.assert_awaited_once()


# ---------------------------------------------------------------------------
# _get_signed_url — cannot be versioned warning path
# ---------------------------------------------------------------------------


class TestGetSignedUrlVersionWarning:
    @pytest.mark.asyncio
    async def test_logs_when_error_message_contains_cannot_version(self):
        bs = _make_bs()
        err_body = {
            "error": {
                "message": "This document cannot be versioned in storage",
            }
        }
        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.json = AsyncMock(return_value=err_body)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        with pytest.raises(aiohttp.ClientError):
            await bs._get_signed_url(mock_session, "http://x/upload", {}, {})

        bs.logger.warning.assert_called()
        warning_msg = str(bs.logger.warning.call_args)
        assert "legacy" in warning_msg or "non-versioned" in warning_msg


# ---------------------------------------------------------------------------
# upload_next_version (local) — ContentTypeError on error body
# ---------------------------------------------------------------------------


class TestUploadNextVersionLocalContentTypeError:
    @pytest.mark.asyncio
    async def test_non_json_error_body_uses_text_branch(self):
        bs = _make_bs()
        bs.config_service.get_config = AsyncMock(side_effect=_auth_side_effect("local"))

        mock_resp = AsyncMock()
        mock_resp.status = 502
        mock_resp.json = AsyncMock(
            side_effect=aiohttp.ContentTypeError(MagicMock(), MagicMock()),
        )
        mock_resp.text = AsyncMock(return_value="bad gateway html")
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
            with pytest.raises(Exception, match="Failed to upload next version"):
                await bs.upload_next_version(
                    "org",
                    "rec",
                    "doc-1",
                    {"data": 1},
                    "vr",
                )


# ---------------------------------------------------------------------------
# _create_metadata_document (local) — ContentTypeError on failed create
# ---------------------------------------------------------------------------


class TestCreateMetadataDocumentLocalContentTypeError:
    @pytest.mark.asyncio
    async def test_non_json_error_response(self):
        bs = _make_bs()
        bs.config_service.get_config = AsyncMock(side_effect=_auth_side_effect("local"))

        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.json = AsyncMock(
            side_effect=aiohttp.ContentTypeError(MagicMock(), MagicMock()),
        )
        mock_resp.text = AsyncMock(return_value="not json")
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
            with pytest.raises(Exception, match="Failed to create metadata document"):
                await bs._create_metadata_document("org", "rec", "vr", {"m": 1})


# ---------------------------------------------------------------------------
# save_reconciliation_metadata — propagate non–versioning failures
# ---------------------------------------------------------------------------


class TestSaveReconciliationMetadataPropagates:
    @pytest.mark.asyncio
    async def test_upload_next_version_unrelated_error_propagates(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(
            return_value={"record_metadata_doc_id": "existing-meta"},
        )
        bs = _make_bs(graph_provider=gp)
        bs.upload_next_version = AsyncMock(side_effect=RuntimeError("s3 outage"))

        with pytest.raises(RuntimeError, match="s3 outage"):
            await bs.save_reconciliation_metadata("o", "r", "vr", {"x": 1})


# ---------------------------------------------------------------------------
# get_reconciliation_metadata — signed URL GET returns non-200
# ---------------------------------------------------------------------------


class TestGetReconciliationMetadataSignedUrlNonSuccess:
    @pytest.mark.asyncio
    async def test_keeps_outer_payload_when_signed_fetch_fails_status(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(
            return_value={"record_metadata_doc_id": "meta-doc"},
        )
        bs = _make_bs(graph_provider=gp)
        bs.config_service.get_config = AsyncMock(side_effect=_auth_side_effect("s3"))

        dl = _resp(200, {"signedUrl": "https://cdn.example/object"})
        nested_fail = AsyncMock()
        nested_fail.status = 403
        nested_fail.__aenter__ = AsyncMock(return_value=nested_fail)
        nested_fail.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=[dl, nested_fail])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.modules.transformers.blob_storage.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            result = await bs.get_reconciliation_metadata("vr-1", "org-1")

        assert result == {"signedUrl": "https://cdn.example/object"}


# ---------------------------------------------------------------------------
# _get_signed_url — non-dict JSON error body
# ---------------------------------------------------------------------------


class TestGetSignedUrlNonDictErrorBody:
    @pytest.mark.asyncio
    async def test_list_error_detail(self):
        bs = _make_bs()
        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.json = AsyncMock(return_value=["list", "error"])
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        with pytest.raises(aiohttp.ClientError, match="Failed with status"):
            await bs._get_signed_url(mock_session, "http://x", {}, {})


class TestGetSignedUrlDictMissingErrorKey:
    @pytest.mark.asyncio
    async def test_dict_without_error_key_uses_full_body_string(self):
        """Covers lines 478–479: fallback ``str(error_response)`` for odd JSON shapes."""
        bs = _make_bs()
        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.json = AsyncMock(return_value={"reason": "other"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        with pytest.raises(aiohttp.ClientError, match="Failed with status"):
            await bs._get_signed_url(mock_session, "http://x", {}, {})


class TestGetSignedUrlBareStatusWhenNoErrorDetail:
    @pytest.mark.asyncio
    async def test_non_json_body_empty_text_raises_without_suffix(self):
        """Covers line 491 when ``error_detail`` is empty after ContentTypeError."""
        bs = _make_bs()
        mock_resp = AsyncMock()
        mock_resp.status = 503
        mock_resp.json = AsyncMock(
            side_effect=aiohttp.ContentTypeError(MagicMock(), MagicMock()),
        )
        mock_resp.text = AsyncMock(return_value="")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        with pytest.raises(aiohttp.ClientError, match="Failed with status 503$"):
            await bs._get_signed_url(mock_session, "http://x", {}, {})
