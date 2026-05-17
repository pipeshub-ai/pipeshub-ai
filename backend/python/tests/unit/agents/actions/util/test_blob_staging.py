"""
Unit tests for ``app.agents.actions.util.blob_staging``.

Covers:
- ``conversation_upload_to_registry_entry`` mapping logic (incl. the
  ``None``-on-missing-keys contract that ``outlook.stage_attachment_to_blob``
  now relies on for safe unpacking).
- ``_session_or_default`` borrow-vs-own session lifecycle (must NOT close
  an injected session; MUST close one it created itself even on error).
- ``_get_storage_auth`` JWT minting + endpoint resolution failure modes.
- ``fetch_blob_bytes`` for all three response shapes (raw bytes, JSON +
  signedUrl, JSON + base64) plus error paths and session-injection.
- ``fetch_staged_document_bytes`` for the S3 fast path, error path, and
  the scoped-fallback path — including the regression that an injected
  session MUST be forwarded down to ``fetch_blob_bytes`` (the connection-
  reuse change in ``upload_file_to_salesforce``).
"""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import jwt
import pytest

from app.agents.actions.util.blob_staging import (
    BlobStagingError,
    StagedDocumentEntry,
    _get_storage_auth,
    _session_or_default,
    conversation_upload_to_registry_entry,
    fetch_blob_bytes,
    fetch_staged_document_bytes,
)
from app.config.constants.service import TokenScopes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(
    *,
    status: int = 200,
    content_type: str = "application/octet-stream",
    read_bytes: bytes = b"",
    json_data: Any = None,
    text_data: str = "",
) -> MagicMock:
    """Build an async-context-manager mock matching aiohttp.ClientResponse."""
    resp = MagicMock()
    resp.status = status
    resp.headers = {"Content-Type": content_type}
    resp.read = AsyncMock(return_value=read_bytes)
    resp.json = AsyncMock(return_value=json_data)
    resp.text = AsyncMock(return_value=text_data)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _mock_session(
    *,
    responses: list[MagicMock] | None = None,
    response: MagicMock | None = None,
) -> MagicMock:
    """Build an async-context-manager mock matching aiohttp.ClientSession.

    ``responses`` queues per-call returns from ``.get()``; ``response`` returns
    the same response for every ``.get()``.
    """
    session = MagicMock()
    if responses is not None:
        session.get = MagicMock(side_effect=responses)
    else:
        session.get = MagicMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


def _mock_config_service(
    *,
    scoped_jwt_secret: str | None = "test-secret",
    cm_endpoint: str | None = "http://cm.local:3001",
) -> MagicMock:
    """Mock ConfigurationService.get_config with sequenced returns.

    Matches the call order in ``_get_storage_auth``: SECRET_KEYS first,
    ENDPOINTS second.
    """
    secret_payload = (
        {"scopedJwtSecret": scoped_jwt_secret} if scoped_jwt_secret else {}
    )
    endpoints_payload: dict[str, Any] = (
        {"cm": {"endpoint": cm_endpoint}} if cm_endpoint else {"cm": {}}
    )
    cfg = MagicMock()
    cfg.get_config = AsyncMock(side_effect=[secret_payload, endpoints_payload])
    return cfg


# ============================================================================
# conversation_upload_to_registry_entry
# ============================================================================


class TestConversationUploadToRegistryEntry:
    def test_signed_url_yields_s3_storage_type(self):
        mapped = conversation_upload_to_registry_entry(
            {"documentId": "doc-1", "signedUrl": "https://s3.example/blob"},
            filename="a.pdf",
            mime_type="application/pdf",
            size_bytes=128,
        )
        assert mapped is not None
        doc_id, entry = mapped
        assert isinstance(entry, StagedDocumentEntry)
        assert doc_id == "doc-1"
        assert entry.storage_type == "s3"
        assert entry.download_url == "https://s3.example/blob"
        assert entry.filename == "a.pdf"
        assert entry.mime_type == "application/pdf"
        assert entry.size_bytes == 128
        assert entry.source is None

    def test_download_url_only_yields_external_storage_type(self):
        mapped = conversation_upload_to_registry_entry(
            {"documentId": "doc-2", "downloadUrl": "http://cm.local/d/doc-2"},
            filename="b.txt",
            mime_type="text/plain",
            size_bytes=10,
        )
        assert mapped is not None
        doc_id, entry = mapped
        assert doc_id == "doc-2"
        assert entry.storage_type == "external"
        assert entry.download_url == "http://cm.local/d/doc-2"

    def test_signed_url_preferred_over_download_url(self):
        mapped = conversation_upload_to_registry_entry(
            {
                "documentId": "doc-3",
                "signedUrl": "https://s3.example/preferred",
                "downloadUrl": "http://cm.local/d/doc-3",
            },
            filename="c",
            mime_type="application/octet-stream",
            size_bytes=1,
        )
        assert mapped is not None
        _, entry = mapped
        assert entry.download_url == "https://s3.example/preferred"
        assert entry.storage_type == "s3"

    def test_source_field_preserved(self):
        mapped = conversation_upload_to_registry_entry(
            {"documentId": "doc-4", "signedUrl": "https://s3/x"},
            filename="d.eml",
            mime_type="message/rfc822",
            size_bytes=42,
            source={
                "platform": "outlook",
                "message_id": "AAMk...",
                "attachment_id": "att-1",
            },
        )
        assert mapped is not None
        _, entry = mapped
        assert entry.source is not None
        assert entry.source.platform == "outlook"
        assert entry.source.message_id == "AAMk..."
        assert entry.source.attachment_id == "att-1"

    def test_source_extra_fields_preserved(self):
        # Future producers (Drive, Box, ...) attach producer-specific
        # breadcrumbs beyond the three named fields. StagedDocumentSource
        # allows extras precisely so we don't need a schema bump per
        # connector — the consumer never reads these anyway.
        mapped = conversation_upload_to_registry_entry(
            {"documentId": "doc-x", "signedUrl": "https://s3/x"},
            filename="x",
            mime_type="application/pdf",
            size_bytes=1,
            source={"platform": "drive", "file_id": "1abc", "revision_id": "r9"},
        )
        assert mapped is not None
        _, entry = mapped
        dumped = entry.source.model_dump()
        assert dumped["file_id"] == "1abc"
        assert dumped["revision_id"] == "r9"

    def test_missing_document_id_returns_none(self):
        # Regression: outlook.stage_attachment_to_blob now unpacks this result
        # only after a None-guard. If this contract changes, the guard breaks.
        result = conversation_upload_to_registry_entry(
            {"signedUrl": "https://s3/x"},
            filename="x",
            mime_type="application/pdf",
            size_bytes=1,
        )
        assert result is None

    def test_missing_both_urls_returns_none(self):
        result = conversation_upload_to_registry_entry(
            {"documentId": "doc-5"},
            filename="x",
            mime_type="application/pdf",
            size_bytes=1,
        )
        assert result is None

    def test_empty_string_document_id_returns_none(self):
        result = conversation_upload_to_registry_entry(
            {"documentId": "", "signedUrl": "https://s3/x"},
            filename="x",
            mime_type="application/pdf",
            size_bytes=1,
        )
        assert result is None

    def test_document_id_coerced_to_str(self):
        mapped = conversation_upload_to_registry_entry(
            {"documentId": 12345, "signedUrl": "https://s3/x"},
            filename="x",
            mime_type="application/pdf",
            size_bytes=1,
        )
        assert mapped is not None
        doc_id, _ = mapped
        assert doc_id == "12345"
        assert isinstance(doc_id, str)


# ============================================================================
# _session_or_default
# ============================================================================


class TestSessionOrDefault:
    @pytest.mark.asyncio
    async def test_borrowed_session_is_yielded_unchanged(self):
        borrowed = MagicMock(spec=aiohttp.ClientSession)
        borrowed.close = AsyncMock()
        borrowed.__aenter__ = AsyncMock(return_value=borrowed)
        borrowed.__aexit__ = AsyncMock(return_value=False)

        async with _session_or_default(borrowed) as http:
            assert http is borrowed

        # Critical invariant: the helper MUST NOT close a borrowed session,
        # or the caller's next request on the shared session would raise
        # "Session is closed". The caller owns the lifecycle.
        borrowed.close.assert_not_called()
        borrowed.__aexit__.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_creates_and_closes_new_session(self):
        created = _mock_session(response=_mock_response())
        with patch(
            "app.agents.actions.util.blob_staging.aiohttp.ClientSession",
            return_value=created,
        ):
            async with _session_or_default(None) as http:
                assert http is created
        created.__aexit__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_owned_session_closed_when_body_raises(self):
        created = _mock_session(response=_mock_response())
        with patch(
            "app.agents.actions.util.blob_staging.aiohttp.ClientSession",
            return_value=created,
        ):
            with pytest.raises(RuntimeError, match="boom"):
                async with _session_or_default(None):
                    raise RuntimeError("boom")
        # The owned session is closed even on exception (the async with
        # aiohttp.ClientSession() inside the helper guarantees teardown).
        created.__aexit__.assert_awaited_once()


# ============================================================================
# _get_storage_auth
# ============================================================================


class TestGetStorageAuth:
    @pytest.mark.asyncio
    async def test_success_returns_bearer_and_endpoint(self):
        cfg = _mock_config_service()
        headers, endpoint = await _get_storage_auth("org-1", cfg)
        assert endpoint == "http://cm.local:3001"
        assert headers["Authorization"].startswith("Bearer ")
        token = headers["Authorization"].removeprefix("Bearer ")
        decoded = jwt.decode(token, "test-secret", algorithms=["HS256"])
        assert decoded["orgId"] == "org-1"
        assert decoded["scopes"] == [TokenScopes.STORAGE_TOKEN.value]

    @pytest.mark.asyncio
    async def test_endpoint_trailing_slash_stripped(self):
        cfg = _mock_config_service(cm_endpoint="http://cm.local:3001/")
        _, endpoint = await _get_storage_auth("org-1", cfg)
        assert endpoint == "http://cm.local:3001"

    @pytest.mark.asyncio
    async def test_missing_org_id_raises(self):
        cfg = _mock_config_service()
        with pytest.raises(BlobStagingError, match="org_id is required"):
            await _get_storage_auth("", cfg)

    @pytest.mark.asyncio
    async def test_missing_secret_raises(self):
        cfg = _mock_config_service(scoped_jwt_secret=None)
        with pytest.raises(BlobStagingError, match="scopedJwtSecret"):
            await _get_storage_auth("org-1", cfg)

    @pytest.mark.asyncio
    async def test_missing_endpoint_falls_back_to_default(self):
        # When ``cm.endpoint`` is missing the helper falls back to
        # DefaultEndpoints.NODEJS_ENDPOINT rather than raising — the only
        # way to actually trigger the "Missing cm endpoint" branch is to
        # also override the default to a falsy value, which is not
        # something a real config can do. We just assert no raise + a
        # non-empty endpoint is returned.
        cfg = _mock_config_service(cm_endpoint=None)
        _, endpoint = await _get_storage_auth("org-1", cfg)
        assert endpoint and isinstance(endpoint, str)


# ============================================================================
# fetch_blob_bytes
# ============================================================================


class TestFetchBlobBytes:
    @pytest.mark.asyncio
    async def test_missing_storage_document_id_raises(self):
        cfg = _mock_config_service()
        with pytest.raises(BlobStagingError, match="storage_document_id"):
            await fetch_blob_bytes(
                org_id="org-1",
                config_service=cfg,
                storage_document_id="",
            )

    @pytest.mark.asyncio
    async def test_raw_bytes_response(self):
        cfg = _mock_config_service()
        resp = _mock_response(
            content_type="application/pdf",
            read_bytes=b"%PDF-1.7\n...",
        )
        session = _mock_session(response=resp)
        with patch(
            "app.agents.actions.util.blob_staging.aiohttp.ClientSession",
            return_value=session,
        ):
            data = await fetch_blob_bytes(
                org_id="org-1",
                config_service=cfg,
                storage_document_id="doc-1",
            )
        assert data == b"%PDF-1.7\n..."

    @pytest.mark.asyncio
    async def test_json_signed_url_triggers_second_get_on_same_session(self):
        cfg = _mock_config_service()
        json_resp = _mock_response(
            content_type="application/json",
            json_data={"signedUrl": "https://s3.example/object?sig=xyz"},
        )
        signed_resp = _mock_response(read_bytes=b"file-bytes")
        session = _mock_session(responses=[json_resp, signed_resp])
        with patch(
            "app.agents.actions.util.blob_staging.aiohttp.ClientSession",
            return_value=session,
        ):
            data = await fetch_blob_bytes(
                org_id="org-1",
                config_service=cfg,
                storage_document_id="doc-1",
            )
        assert data == b"file-bytes"
        # Both hops happened on the SAME session — the entire reason the
        # signed-URL branch was rewritten to reuse the open session.
        assert session.get.call_count == 2

    @pytest.mark.asyncio
    async def test_json_base64_fallback(self):
        cfg = _mock_config_service()
        payload = b"hello world"
        json_resp = _mock_response(
            content_type="application/json",
            json_data={"base64": base64.b64encode(payload).decode("ascii")},
        )
        session = _mock_session(response=json_resp)
        with patch(
            "app.agents.actions.util.blob_staging.aiohttp.ClientSession",
            return_value=session,
        ):
            data = await fetch_blob_bytes(
                org_id="org-1",
                config_service=cfg,
                storage_document_id="doc-1",
            )
        assert data == payload

    @pytest.mark.asyncio
    async def test_json_without_signed_url_or_base64_raises(self):
        cfg = _mock_config_service()
        json_resp = _mock_response(
            content_type="application/json",
            json_data={"unexpected": True},
        )
        session = _mock_session(response=json_resp)
        with patch(
            "app.agents.actions.util.blob_staging.aiohttp.ClientSession",
            return_value=session,
        ):
            with pytest.raises(
                BlobStagingError, match="signedUrl/base64",
            ):
                await fetch_blob_bytes(
                    org_id="org-1",
                    config_service=cfg,
                    storage_document_id="doc-1",
                )

    @pytest.mark.asyncio
    async def test_non_200_raises_with_status_and_detail(self):
        cfg = _mock_config_service()
        resp = _mock_response(status=404, text_data="Not Found")
        session = _mock_session(response=resp)
        with patch(
            "app.agents.actions.util.blob_staging.aiohttp.ClientSession",
            return_value=session,
        ):
            with pytest.raises(BlobStagingError, match=r"\[404\]"):
                await fetch_blob_bytes(
                    org_id="org-1",
                    config_service=cfg,
                    storage_document_id="doc-1",
                )

    @pytest.mark.asyncio
    async def test_signed_url_non_200_raises(self):
        cfg = _mock_config_service()
        json_resp = _mock_response(
            content_type="application/json",
            json_data={"signedUrl": "https://s3.example/object"},
        )
        signed_resp = _mock_response(status=403, text_data="Forbidden")
        session = _mock_session(responses=[json_resp, signed_resp])
        with patch(
            "app.agents.actions.util.blob_staging.aiohttp.ClientSession",
            return_value=session,
        ):
            with pytest.raises(BlobStagingError, match="Signed URL fetch"):
                await fetch_blob_bytes(
                    org_id="org-1",
                    config_service=cfg,
                    storage_document_id="doc-1",
                )

    @pytest.mark.asyncio
    async def test_injected_session_is_used_and_not_closed(self):
        # Regression: when a caller injects a session, the helper must
        # use it (no patching of aiohttp.ClientSession is required), and
        # must not close it on exit.
        cfg = _mock_config_service()
        resp = _mock_response(
            content_type="application/pdf",
            read_bytes=b"pdf",
        )
        injected = _mock_session(response=resp)
        injected.close = AsyncMock()

        data = await fetch_blob_bytes(
            org_id="org-1",
            config_service=cfg,
            storage_document_id="doc-1",
            session=injected,
        )
        assert data == b"pdf"
        assert injected.get.call_count == 1
        # Injected sessions belong to the caller; we did not close it.
        injected.close.assert_not_called()


# ============================================================================
# fetch_staged_document_bytes
# ============================================================================


def _valid_entry(
    *,
    storage_type: str = "s3",
    download_url: str = "https://s3.example/object",
    filename: str = "a.pdf",
    mime_type: str = "application/pdf",
    size_bytes: int = 1,
) -> dict[str, Any]:
    """Build a complete registry-entry dict for fetch tests.

    ``fetch_staged_document_bytes`` validates its ``entry`` kwarg into a
    ``StagedDocumentEntry`` at the boundary (so checkpointer round-trips
    that downgrade models to dicts still work). Tests therefore have to
    supply every required field; this helper keeps the per-test seeds
    focused on the field actually under test.
    """
    return {
        "storage_type": storage_type,
        "download_url": download_url,
        "filename": filename,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
    }


class TestFetchStagedDocumentBytes:
    @pytest.mark.asyncio
    async def test_s3_path_direct_get(self):
        cfg = _mock_config_service()
        resp = _mock_response(read_bytes=b"s3-payload")
        session = _mock_session(response=resp)
        with patch(
            "app.agents.actions.util.blob_staging.aiohttp.ClientSession",
            return_value=session,
        ):
            data = await fetch_staged_document_bytes(
                document_id="doc-1",
                entry=_valid_entry(storage_type="s3"),
                org_id="org-1",
                config_service=cfg,
            )
        assert data == b"s3-payload"
        # Should NOT have minted a storage JWT — the S3 branch skips that.
        cfg.get_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_s3_path_accepts_validated_model(self):
        # Producers (outlook.stage_attachment_to_blob, etc.) write a
        # StagedDocumentEntry directly into chat_state — the function
        # must honor it without re-validating it through dict-coercion.
        cfg = _mock_config_service()
        resp = _mock_response(read_bytes=b"model-payload")
        session = _mock_session(response=resp)
        with patch(
            "app.agents.actions.util.blob_staging.aiohttp.ClientSession",
            return_value=session,
        ):
            data = await fetch_staged_document_bytes(
                document_id="doc-1",
                entry=StagedDocumentEntry(**_valid_entry(storage_type="s3")),
                org_id="org-1",
                config_service=cfg,
            )
        assert data == b"model-payload"

    @pytest.mark.asyncio
    async def test_malformed_entry_dict_rejected_at_boundary(self):
        # A dict missing required fields (e.g. download_url) used to
        # surface as a RuntimeError from the S3 branch; with the
        # boundary coercion it now fails fast as a ValidationError,
        # which the salesforce consumer wraps into "corrupt_registry_entry".
        cfg = _mock_config_service()
        with pytest.raises(ValueError):  # pydantic.ValidationError is a ValueError
            await fetch_staged_document_bytes(
                document_id="doc-1",
                entry={"storage_type": "s3"},
                org_id="org-1",
                config_service=cfg,
            )

    @pytest.mark.asyncio
    async def test_s3_path_non_200_raises_runtime_error(self):
        cfg = _mock_config_service()
        resp = _mock_response(status=500, text_data="bad")
        session = _mock_session(response=resp)
        with patch(
            "app.agents.actions.util.blob_staging.aiohttp.ClientSession",
            return_value=session,
        ):
            with pytest.raises(RuntimeError, match="HTTP 500"):
                await fetch_staged_document_bytes(
                    document_id="doc-1",
                    entry=_valid_entry(storage_type="s3"),
                    org_id="org-1",
                    config_service=cfg,
                )

    @pytest.mark.asyncio
    async def test_external_storage_type_delegates_to_fetch_blob_bytes(self):
        cfg = _mock_config_service()
        with patch(
            "app.agents.actions.util.blob_staging.fetch_blob_bytes",
            new=AsyncMock(return_value=b"scoped-bytes"),
        ) as mock_fbb:
            data = await fetch_staged_document_bytes(
                document_id="doc-1",
                entry=_valid_entry(storage_type="external"),
                org_id="org-1",
                config_service=cfg,
            )
        assert data == b"scoped-bytes"
        mock_fbb.assert_awaited_once_with(
            org_id="org-1",
            config_service=cfg,
            storage_document_id="doc-1",
            session=None,
        )

    @pytest.mark.asyncio
    async def test_injected_session_forwarded_to_fetch_blob_bytes(self):
        # Regression: ``upload_file_to_salesforce`` opens one session for
        # the whole batch and passes it down. If this kwarg isn't forwarded
        # to ``fetch_blob_bytes``, the scoped-path fetches silently revert
        # to per-call sessions and you lose intra-batch keep-alive.
        cfg = _mock_config_service()
        injected = MagicMock(spec=aiohttp.ClientSession)
        with patch(
            "app.agents.actions.util.blob_staging.fetch_blob_bytes",
            new=AsyncMock(return_value=b"x"),
        ) as mock_fbb:
            await fetch_staged_document_bytes(
                document_id="doc-1",
                entry=_valid_entry(storage_type="external"),
                org_id="org-1",
                config_service=cfg,
                session=injected,
            )
        assert mock_fbb.await_args.kwargs["session"] is injected

    @pytest.mark.asyncio
    async def test_injected_session_used_on_s3_path(self):
        # On the S3 fast path the helper makes the GET itself, so it
        # must also honor the injected session rather than spinning up
        # a fresh one.
        cfg = _mock_config_service()
        resp = _mock_response(read_bytes=b"s3-bytes")
        injected = _mock_session(response=resp)
        injected.close = AsyncMock()

        data = await fetch_staged_document_bytes(
            document_id="doc-1",
            entry=_valid_entry(storage_type="s3"),
            org_id="org-1",
            config_service=cfg,
            session=injected,
        )
        assert data == b"s3-bytes"
        injected.get.assert_called_once()
        injected.close.assert_not_called()
