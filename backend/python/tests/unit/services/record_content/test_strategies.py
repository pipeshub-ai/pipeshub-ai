"""Unit tests for record-content fetch strategies.

Covers:
- _guess_mime: MIME type from attributes, filename fallback, default fallback
- _record_attr: attribute access on dicts (camelCase / snake_case) and objects
- BlobBackedContentStrategy.supports with various origin types
- ConnectorBackedContentStrategy.supports with various origin types
- BlobBackedContentStrategy.fetch: happy path, version resolution, too-large, missing ID
- ConnectorBackedContentStrategy.fetch: happy path, 404, 403, 500, too-large, missing secret
- build_resolved_content: assembly from record + raw bytes
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from app.config.constants.arangodb import OriginTypes
from app.services.record_content.models import (
    RecordContentUnavailableError,
    RecordTooLargeError,
    ResolvedRecordContent,
)
from app.services.record_content.strategies import (
    BlobBackedContentStrategy,
    ConnectorBackedContentStrategy,
    _guess_mime,
    _record_attr,
    build_resolved_content,
)


# ---------------------------------------------------------------------------
# _guess_mime
# ---------------------------------------------------------------------------

class TestGuessMime:
    def test_mime_type_attr(self):
        record = SimpleNamespace(mime_type="application/pdf")
        assert _guess_mime(record) == "application/pdf"

    def test_mime_type_from_dict_camel(self):
        record = {"mimeType": "text/html"}
        assert _guess_mime(record) == "text/html"

    def test_fallback_to_filename(self):
        record = SimpleNamespace(mime_type=None, record_name="report.json")
        assert _guess_mime(record) == "application/json"

    def test_fallback_to_dict_filename(self):
        record = {"recordName": "image.png"}
        assert _guess_mime(record) == "image/png"

    def test_default_fallback(self):
        record = SimpleNamespace(mime_type=None, record_name=None)
        assert _guess_mime(record) == "application/octet-stream"

    def test_custom_default_fallback(self):
        record = {}
        assert _guess_mime(record, fallback="text/plain") == "text/plain"

    def test_unknown_extension_falls_back(self):
        record = SimpleNamespace(mime_type=None, record_name="data.xyz123unknown")
        assert _guess_mime(record) == "application/octet-stream"


# ---------------------------------------------------------------------------
# _record_attr
# ---------------------------------------------------------------------------

class TestRecordAttr:
    def test_dict_camel_case(self):
        record = {"externalRecordId": "ext-1"}
        assert _record_attr(record, "externalRecordId", "external_record_id") == "ext-1"

    def test_dict_snake_case(self):
        record = {"external_record_id": "ext-2"}
        assert _record_attr(record, "externalRecordId", "external_record_id") == "ext-2"

    def test_dict_prefers_camel(self):
        record = {"externalRecordId": "camel", "external_record_id": "snake"}
        assert _record_attr(record, "externalRecordId", "external_record_id") == "camel"

    def test_dict_missing_returns_none(self):
        assert _record_attr({}, "foo", "bar") is None

    def test_object_snake_attr(self):
        obj = SimpleNamespace(external_record_id="obj-1")
        assert _record_attr(obj, "externalRecordId", "external_record_id") == "obj-1"

    def test_object_camel_attr(self):
        obj = SimpleNamespace(externalRecordId="obj-2")
        assert _record_attr(obj, "externalRecordId", "external_record_id") == "obj-2"

    def test_object_missing_returns_none(self):
        obj = SimpleNamespace()
        assert _record_attr(obj, "foo", "bar") is None


# ---------------------------------------------------------------------------
# BlobBackedContentStrategy.supports
# ---------------------------------------------------------------------------

class TestBlobSupports:
    def setup_method(self):
        self.strategy = BlobBackedContentStrategy()

    def test_supports_origin_enum_upload(self):
        record = SimpleNamespace(origin=OriginTypes.UPLOAD)
        assert self.strategy.supports(record) is True

    def test_supports_origin_string_upload(self):
        record = {"origin": "UPLOAD"}
        assert self.strategy.supports(record) is True

    def test_supports_origin_string_upload_lowercase(self):
        record = {"origin": "upload"}
        assert self.strategy.supports(record) is True

    def test_rejects_connector_enum(self):
        record = SimpleNamespace(origin=OriginTypes.CONNECTOR)
        assert self.strategy.supports(record) is False

    def test_rejects_connector_string(self):
        record = {"origin": "CONNECTOR"}
        assert self.strategy.supports(record) is False

    def test_rejects_none_origin(self):
        record = {"origin": None}
        assert self.strategy.supports(record) is False

    def test_rejects_missing_origin(self):
        record = {}
        assert self.strategy.supports(record) is False


# ---------------------------------------------------------------------------
# ConnectorBackedContentStrategy.supports
# ---------------------------------------------------------------------------

class TestConnectorSupports:
    def setup_method(self):
        self.strategy = ConnectorBackedContentStrategy()

    def test_supports_connector_enum(self):
        record = SimpleNamespace(origin=OriginTypes.CONNECTOR)
        assert self.strategy.supports(record) is True

    def test_supports_connector_string(self):
        record = {"origin": "CONNECTOR"}
        assert self.strategy.supports(record) is True

    def test_supports_connector_string_lowercase(self):
        record = {"origin": "connector"}
        assert self.strategy.supports(record) is True

    def test_rejects_upload_enum(self):
        record = SimpleNamespace(origin=OriginTypes.UPLOAD)
        assert self.strategy.supports(record) is False

    def test_rejects_upload_string(self):
        record = {"origin": "UPLOAD"}
        assert self.strategy.supports(record) is False

    def test_rejects_none_origin(self):
        record = SimpleNamespace(origin=None)
        assert self.strategy.supports(record) is False


# ---------------------------------------------------------------------------
# build_resolved_content
# ---------------------------------------------------------------------------

class TestBuildResolvedContent:
    def test_basic_assembly(self):
        record = {
            "_key": "rec-1",
            "recordName": "notes.txt",
            "mimeType": "text/plain",
        }
        content = b"hello world"
        result = build_resolved_content(record, content, version=3, source="blob")

        assert isinstance(result, ResolvedRecordContent)
        assert result.record_id == "rec-1"
        assert result.filename == "notes.txt"
        assert result.mime_type == "text/plain"
        assert result.size_bytes == 11
        assert result.content == b"hello world"
        assert result.version == 3
        assert result.source == "blob"

    def test_filename_fallback_to_name(self):
        record = {"_key": "rec-2", "name": "fallback.pdf"}
        result = build_resolved_content(record, b"", version=None, source="connector")
        assert result.filename == "fallback.pdf"

    def test_filename_fallback_to_record_id(self):
        record = {"_key": "rec-3"}
        result = build_resolved_content(record, b"x", version=None, source="blob")
        assert result.filename == "rec-3"

    def test_mime_guessed_from_filename(self):
        record = {"_key": "r", "recordName": "data.json"}
        result = build_resolved_content(record, b"{}", version=None, source="blob")
        assert result.mime_type == "application/json"

    def test_version_none(self):
        record = {"_key": "r"}
        result = build_resolved_content(record, b"", version=None, source="blob")
        assert result.version is None


# ---------------------------------------------------------------------------
# BlobBackedContentStrategy.fetch
# ---------------------------------------------------------------------------

class TestBlobFetch:
    def setup_method(self):
        self.strategy = BlobBackedContentStrategy()
        self.actor = SimpleNamespace(org_id="org-1", user_id="user-1")
        self.config_service = AsyncMock()

    @pytest.mark.asyncio
    async def test_happy_path_no_version(self):
        record = {"externalRecordId": "ext-1", "_key": "rec-1"}
        with patch(
            "app.services.record_content.strategies.fetch_blob_bytes",
            new_callable=AsyncMock,
            return_value=b"content-bytes",
        ):
            data = await self.strategy.fetch(
                record,
                actor=self.actor,
                version=None,
                max_bytes=1_000_000,
                session=None,
                config_service=self.config_service,
            )
        assert data == b"content-bytes"

    @pytest.mark.asyncio
    async def test_missing_external_record_id_raises(self):
        record = {"_key": "rec-1"}
        with pytest.raises(RecordContentUnavailableError, match="no externalRecordId"):
            await self.strategy.fetch(
                record,
                actor=self.actor,
                version=None,
                max_bytes=1_000_000,
                session=None,
                config_service=self.config_service,
            )

    @pytest.mark.asyncio
    async def test_too_large_raises(self):
        record = {"externalRecordId": "ext-1", "_key": "rec-1"}
        with patch(
            "app.services.record_content.strategies.fetch_blob_bytes",
            new_callable=AsyncMock,
            return_value=b"x" * 200,
        ):
            with pytest.raises(RecordTooLargeError) as exc_info:
                await self.strategy.fetch(
                    record,
                    actor=self.actor,
                    version=None,
                    max_bytes=100,
                    session=None,
                    config_service=self.config_service,
                )
            assert exc_info.value.size_bytes == 200
            assert exc_info.value.max_bytes == 100

    @pytest.mark.asyncio
    async def test_fetch_blob_failure_wraps(self):
        record = {"externalRecordId": "ext-1", "_key": "rec-1"}
        with patch(
            "app.services.record_content.strategies.fetch_blob_bytes",
            new_callable=AsyncMock,
            side_effect=RuntimeError("network down"),
        ):
            with pytest.raises(RecordContentUnavailableError, match="Blob fetch failed"):
                await self.strategy.fetch(
                    record,
                    actor=self.actor,
                    version=None,
                    max_bytes=1_000_000,
                    session=None,
                    config_service=self.config_service,
                )

    @pytest.mark.asyncio
    async def test_version_resolution(self):
        record = {
            "externalRecordId": "ext-1",
            "_key": "rec-1",
            "version": 2,
            "versions": [{"registryVersion": 1, "storageVersion": 0}, {"registryVersion": 2, "storageVersion": 1}],
        }
        with patch(
            "app.services.record_content.strategies.resolve_storage_version",
            return_value=1,
        ) as mock_resolve, patch(
            "app.services.record_content.strategies.fetch_blob_bytes",
            new_callable=AsyncMock,
            return_value=b"v1-content",
        ) as mock_fetch:
            data = await self.strategy.fetch(
                record,
                actor=self.actor,
                version=1,
                max_bytes=1_000_000,
                session=None,
                config_service=self.config_service,
            )
        assert data == b"v1-content"
        mock_resolve.assert_called_once_with(2, record["versions"], 1)
        mock_fetch.assert_called_once()
        assert mock_fetch.call_args.kwargs["version"] == 1

    @pytest.mark.asyncio
    async def test_version_resolution_failure_raises(self):
        record = {"externalRecordId": "ext-1", "_key": "rec-1", "version": 2, "versions": []}
        with patch(
            "app.services.record_content.strategies.resolve_storage_version",
            side_effect=ValueError("version out of range"),
        ):
            with pytest.raises(RecordContentUnavailableError, match="Cannot resolve version"):
                await self.strategy.fetch(
                    record,
                    actor=self.actor,
                    version=99,
                    max_bytes=1_000_000,
                    session=None,
                    config_service=self.config_service,
                )


# ---------------------------------------------------------------------------
# ConnectorBackedContentStrategy.fetch
# ---------------------------------------------------------------------------

def _make_config_service(scoped_jwt_secret="test-secret", connector_endpoint="http://connectors:8088"):
    svc = AsyncMock()

    async def _get_config(path, **kwargs):
        if "secret" in path.lower() or "secretKeys" in path:
            return {"scopedJwtSecret": scoped_jwt_secret}
        if "endpoint" in path.lower():
            return {"connectors": {"endpoint": connector_endpoint}}
        return {}

    svc.get_config = AsyncMock(side_effect=_get_config)
    return svc


class TestConnectorFetch:
    def setup_method(self):
        self.strategy = ConnectorBackedContentStrategy()
        self.actor = SimpleNamespace(org_id="org-1", user_id="user-1")

    @pytest.mark.asyncio
    async def test_missing_record_id_raises(self):
        record = {}
        with pytest.raises(RecordContentUnavailableError, match="no id"):
            await self.strategy.fetch(
                record,
                actor=self.actor,
                version=None,
                max_bytes=1_000_000,
                session=None,
                config_service=_make_config_service(),
            )

    @pytest.mark.asyncio
    async def test_missing_scoped_jwt_secret_raises(self):
        record = {"_key": "rec-1"}
        config_service = _make_config_service(scoped_jwt_secret=None)
        with pytest.raises(RecordContentUnavailableError, match="scopedJwtSecret"):
            await self.strategy.fetch(
                record,
                actor=self.actor,
                version=None,
                max_bytes=1_000_000,
                session=None,
                config_service=config_service,
            )

    @pytest.mark.asyncio
    async def test_happy_path(self):
        record = {"_key": "rec-1"}
        content = b"connector-content"

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Length": str(len(content))}
        mock_resp.read = AsyncMock(return_value=content)

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_ctx)

        data = await self.strategy.fetch(
            record,
            actor=self.actor,
            version=None,
            max_bytes=1_000_000,
            session=mock_session,
            config_service=_make_config_service(),
        )
        assert data == content

    @pytest.mark.asyncio
    async def test_404_raises_unavailable(self):
        record = {"_key": "rec-1"}

        mock_resp = AsyncMock()
        mock_resp.status = 404

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_ctx)

        with pytest.raises(RecordContentUnavailableError, match="not found"):
            await self.strategy.fetch(
                record,
                actor=self.actor,
                version=None,
                max_bytes=1_000_000,
                session=mock_session,
                config_service=_make_config_service(),
            )

    @pytest.mark.asyncio
    async def test_403_raises_access_denied(self):
        record = {"_key": "rec-1"}

        mock_resp = AsyncMock()
        mock_resp.status = 403

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_ctx)

        with pytest.raises(RecordContentUnavailableError, match="denied"):
            await self.strategy.fetch(
                record,
                actor=self.actor,
                version=None,
                max_bytes=1_000_000,
                session=mock_session,
                config_service=_make_config_service(),
            )

    @pytest.mark.asyncio
    async def test_500_raises_unavailable(self):
        record = {"_key": "rec-1"}

        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="Internal Server Error")

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_ctx)

        with pytest.raises(RecordContentUnavailableError, match="returned 500"):
            await self.strategy.fetch(
                record,
                actor=self.actor,
                version=None,
                max_bytes=1_000_000,
                session=mock_session,
                config_service=_make_config_service(),
            )

    @pytest.mark.asyncio
    async def test_content_length_too_large_raises(self):
        record = {"_key": "rec-1"}

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Length": "999999"}

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_ctx)

        with pytest.raises(RecordTooLargeError) as exc_info:
            await self.strategy.fetch(
                record,
                actor=self.actor,
                version=None,
                max_bytes=100,
                session=mock_session,
                config_service=_make_config_service(),
            )
        assert exc_info.value.size_bytes == 999999
        assert exc_info.value.max_bytes == 100

    @pytest.mark.asyncio
    async def test_actual_bytes_too_large_raises(self):
        record = {"_key": "rec-1"}
        big_content = b"x" * 200

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {}
        mock_resp.read = AsyncMock(return_value=big_content)

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_ctx)

        with pytest.raises(RecordTooLargeError) as exc_info:
            await self.strategy.fetch(
                record,
                actor=self.actor,
                version=None,
                max_bytes=100,
                session=mock_session,
                config_service=_make_config_service(),
            )
        assert exc_info.value.size_bytes == 200

    @pytest.mark.asyncio
    async def test_version_appended_to_url(self):
        record = {"_key": "rec-1"}
        content = b"v2"

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {}
        mock_resp.read = AsyncMock(return_value=content)

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_ctx)

        await self.strategy.fetch(
            record,
            actor=self.actor,
            version=2,
            max_bytes=1_000_000,
            session=mock_session,
            config_service=_make_config_service(),
        )
        call_url = mock_session.get.call_args[0][0]
        assert "?version=2" in call_url

    @pytest.mark.asyncio
    async def test_no_session_creates_own(self):
        record = {"_key": "rec-1"}
        content = b"owned-session"

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {}
        mock_resp.read = AsyncMock(return_value=content)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session_instance = MagicMock(spec=aiohttp.ClientSession)
        mock_session_instance.get = MagicMock(return_value=mock_ctx)

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.record_content.strategies.aiohttp.ClientSession", return_value=mock_session_ctx):
            data = await self.strategy.fetch(
                record,
                actor=self.actor,
                version=None,
                max_bytes=1_000_000,
                session=None,
                config_service=_make_config_service(),
            )
        assert data == content

    @pytest.mark.asyncio
    async def test_network_error_wraps(self):
        record = {"_key": "rec-1"}

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("connection refused"))
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_ctx)

        with pytest.raises(RecordContentUnavailableError, match="Connector content fetch failed"):
            await self.strategy.fetch(
                record,
                actor=self.actor,
                version=None,
                max_bytes=1_000_000,
                session=mock_session,
                config_service=_make_config_service(),
            )

    @pytest.mark.asyncio
    async def test_invalid_content_length_ignored(self):
        record = {"_key": "rec-1"}
        content = b"ok"

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Length": "not-a-number"}
        mock_resp.read = AsyncMock(return_value=content)

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_ctx)

        data = await self.strategy.fetch(
            record,
            actor=self.actor,
            version=None,
            max_bytes=1_000_000,
            session=mock_session,
            config_service=_make_config_service(),
        )
        assert data == content
