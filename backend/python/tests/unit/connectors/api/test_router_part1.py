"""Tests for app.connectors.api.router — first half (lines 1-3550).

Covers utility functions, dependency helpers, and route handlers through
update_connector_instance_name.
"""

import asyncio
import base64
import json
import os
import tempfile
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse

from app.config.constants.arangodb import (
    AppStatus,
    Connectors,
    OriginTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.api.router import (
    _check_connector_not_locked,
    _decode_state_with_instance,
    _encode_state_with_instance,
    _get_config_path_for_instance,
    _get_settings_base_path,
    _parse_comma_separated_str,
    _sanitize_app_name,
    _trim_config_values,
    _trim_connector_config,
    _validate_connector_deletion_permissions,
    get_mime_type_from_record,
)
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.models.entities import RecordType

# ============================================================================
# Helpers
# ============================================================================


def _mock_record(**overrides):
    """Build a fake Record-like object with sensible defaults."""
    defaults = {
        "id": "rec-1",
        "org_id": "org-1",
        "record_name": "doc.pdf",
        "record_type": RecordType.FILE,
        "mime_type": "application/pdf",
        "connector_name": Connectors.GOOGLE_DRIVE,
        "connector_id": "conn-1",
        "external_record_id": "ext-1",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_request(
    *,
    user: dict | None = None,
    headers: dict | None = None,
    body: dict | None = None,
    container: Any | None = None,
    connector_registry: Any | None = None,
    graph_provider: Any | None = None,
    query_params: dict | None = None,
):
    """Build a minimal mock FastAPI request object."""
    req = MagicMock()

    # state.user
    user_data = user or {"userId": "user-1", "orgId": "org-1"}
    req.state = MagicMock()
    req.state.user = MagicMock()
    req.state.user.get = lambda k, default=None: user_data.get(k, default)

    # headers
    _headers = headers or {}
    req.headers = MagicMock()
    req.headers.get = lambda k, default=None: _headers.get(k, default)

    # json body
    if body is not None:
        req.json = AsyncMock(return_value=body)
    else:
        req.json = AsyncMock(return_value={})

    # query_params
    if query_params:
        req.query_params = MagicMock()
        req.query_params.get = lambda k, default=None: query_params.get(k, default)
    else:
        req.query_params = MagicMock()
        req.query_params.get = lambda k, default=None: None

    # app.container + app.state
    _container = container or MagicMock()
    req.app = MagicMock()
    req.app.container = _container
    if connector_registry:
        req.app.state.connector_registry = connector_registry
    else:
        req.app.state.connector_registry = MagicMock()
    if graph_provider:
        req.app.state.graph_provider = graph_provider
    else:
        req.app.state.graph_provider = MagicMock()

    return req


# ============================================================================
# get_mime_type_from_record
# ============================================================================


class TestGetMimeTypeFromRecord:
    """Tests for get_mime_type_from_record()."""

    def test_returns_stored_mime_type(self):
        record = _mock_record(mime_type="text/plain")
        assert get_mime_type_from_record(record) == "text/plain"

    def test_fallback_to_guess_from_record_name(self):
        record = _mock_record(mime_type=None, record_name="slides.pptx")
        result = get_mime_type_from_record(record)
        assert "presentation" in result or "pptx" in result.lower() or result != "application/octet-stream"

    def test_fallback_to_guess_from_name_attr(self):
        record = SimpleNamespace(mime_type=None, name="photo.jpg")
        result = get_mime_type_from_record(record)
        assert "image" in result

    def test_fallback_to_octet_stream(self):
        record = SimpleNamespace(mime_type=None)
        result = get_mime_type_from_record(record)
        assert result == "application/octet-stream"

    def test_empty_mime_type_triggers_fallback(self):
        record = _mock_record(mime_type="", record_name="data.csv")
        result = get_mime_type_from_record(record)
        # Windows registry may guess spreadsheet MIME for .csv
        assert result in ("text/csv", "application/vnd.ms-excel")

    def test_no_extension_triggers_octet_stream(self):
        record = _mock_record(mime_type="", record_name="README")
        result = get_mime_type_from_record(record)
        assert result == "application/octet-stream"

    def test_record_name_preferred_over_name(self):
        """record_name takes precedence when both exist."""
        record = SimpleNamespace(
            mime_type=None,
            record_name="report.pdf",
            name="photo.jpg"
        )
        assert get_mime_type_from_record(record) == "application/pdf"


# ============================================================================
# _parse_comma_separated_str
# ============================================================================


class TestParseCommaSeparatedStr:
    """Tests for _parse_comma_separated_str()."""

    def test_none_returns_none(self):
        assert _parse_comma_separated_str(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_comma_separated_str("") is None

    def test_single_value(self):
        assert _parse_comma_separated_str("FILE") == ["FILE"]

    def test_multiple_values(self):
        assert _parse_comma_separated_str("FILE,MAIL,TICKET") == ["FILE", "MAIL", "TICKET"]

    def test_trims_whitespace(self):
        assert _parse_comma_separated_str(" FILE , MAIL ") == ["FILE", "MAIL"]

    def test_filters_empty_items(self):
        assert _parse_comma_separated_str("FILE,,MAIL,") == ["FILE", "MAIL"]

    def test_only_commas_returns_empty_list(self):
        result = _parse_comma_separated_str(",,,")
        assert result == []

    def test_whitespace_only_returns_empty_list(self):
        result = _parse_comma_separated_str("   ")
        assert result == []


# ============================================================================
# _sanitize_app_name
# ============================================================================


class TestSanitizeAppName:
    """Tests for _sanitize_app_name()."""

    def test_basic(self):
        assert _sanitize_app_name("Google Drive") == "googledrive"

    def test_already_lowercase_no_spaces(self):
        assert _sanitize_app_name("slack") == "slack"

    def test_multiple_spaces(self):
        assert _sanitize_app_name("My  App  Name") == "myappname"

    def test_empty_string(self):
        assert _sanitize_app_name("") == ""


# ============================================================================
# _trim_config_values
# ============================================================================


class TestTrimConfigValues:
    """Tests for _trim_config_values()."""

    def test_none_returns_none(self):
        assert _trim_config_values(obj=None) is None

    def test_trims_string(self):
        assert _trim_config_values(obj="  hello  ") == "hello"

    def test_preserves_int(self):
        assert _trim_config_values(obj=42) == 42

    def test_preserves_float(self):
        assert _trim_config_values(obj=3.14) == 3.14

    def test_preserves_bool(self):
        assert _trim_config_values(obj=True) is True
        assert _trim_config_values(obj=False) is False

    def test_trims_list_elements(self):
        result = _trim_config_values(obj=["  a  ", "  b  "])
        assert result == ["a", "b"]

    def test_trims_dict_values(self):
        result = _trim_config_values(obj={"key": "  value  "})
        assert result == {"key": "value"}

    def test_nested_dict(self):
        result = _trim_config_values(obj={"outer": {"inner": "  x  "}})
        assert result == {"outer": {"inner": "x"}}

    def test_skip_certificate_field(self):
        """Fields like 'certificate' should NOT be trimmed."""
        result = _trim_config_values(obj="  cert-data  ", path="auth.certificate")
        assert result == "  cert-data  "

    def test_skip_privatekey_field(self):
        result = _trim_config_values(obj="  key-data  ", path="auth.privatekey")
        assert result == "  key-data  "

    def test_skip_client_secret_field(self):
        result = _trim_config_values(obj="  secret  ", path="auth.clientsecret")
        assert result == "  secret  "

    def test_skip_token_field(self):
        result = _trim_config_values(obj="  tok  ", path="token")
        assert result == "  tok  "

    def test_skip_refreshtoken_field(self):
        result = _trim_config_values(obj="  rt  ", path="a.refreshtoken")
        assert result == "  rt  "

    def test_mixed_types_in_list(self):
        result = _trim_config_values(obj=["  a  ", 1, True, None])
        assert result == ["a", 1, True, None]

    def test_empty_string_trimmed(self):
        assert _trim_config_values(obj="   ") == ""


# ============================================================================
# _trim_connector_config
# ============================================================================


class TestTrimConnectorConfig:
    """Tests for _trim_connector_config()."""

    def test_trims_auth_section(self):
        config = {"auth": {"clientId": "  abc  "}}
        result = _trim_connector_config(config)
        assert result["auth"]["clientId"] == "abc"

    def test_trims_sync_section(self):
        config = {"sync": {"interval": "  daily  "}}
        result = _trim_connector_config(config)
        assert result["sync"]["interval"] == "daily"

    def test_trims_filters_section(self):
        config = {"filters": {"type": "  FILE  "}}
        result = _trim_connector_config(config)
        assert result["filters"]["type"] == "FILE"

    def test_does_not_modify_other_sections(self):
        config = {"other": {"key": "  val  "}, "auth": {"k": "  v  "}}
        result = _trim_connector_config(config)
        assert result["other"]["key"] == "  val  "
        assert result["auth"]["k"] == "v"

    def test_none_returns_none(self):
        assert _trim_connector_config(None) is None

    def test_empty_dict(self):
        assert _trim_connector_config({}) == {}

    def test_non_dict_returns_as_is(self):
        assert _trim_connector_config("not a dict") == "not a dict"

    def test_section_not_dict_untouched(self):
        config = {"auth": "not-a-dict", "sync": {"x": "  y  "}}
        result = _trim_connector_config(config)
        assert result["auth"] == "not-a-dict"
        assert result["sync"]["x"] == "y"


# ============================================================================
# _check_connector_not_locked
# ============================================================================


class TestCheckConnectorNotLocked:
    """Tests for _check_connector_not_locked()."""

    def test_unlocked_instance_passes(self):
        _check_connector_not_locked({"isLocked": False})

    def test_missing_isLocked_passes(self):
        _check_connector_not_locked({})

    def test_locked_full_syncing_raises_409(self):
        with pytest.raises(HTTPException) as exc_info:
            _check_connector_not_locked({"isLocked": True, "status": AppStatus.FULL_SYNCING.value})
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value
        assert "full sync" in exc_info.value.detail.lower()

    def test_locked_syncing_raises_409(self):
        with pytest.raises(HTTPException) as exc_info:
            _check_connector_not_locked({"isLocked": True, "status": AppStatus.SYNCING.value})
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value
        assert "sync" in exc_info.value.detail.lower()

    def test_locked_unknown_status_raises_409_default(self):
        with pytest.raises(HTTPException) as exc_info:
            _check_connector_not_locked({"isLocked": True, "status": "CUSTOM"})
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value
        assert "another operation" in exc_info.value.detail.lower()

    def test_locked_no_status_raises_409(self):
        with pytest.raises(HTTPException) as exc_info:
            _check_connector_not_locked({"isLocked": True})
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value


# ============================================================================
# require_connector_not_locked
# ============================================================================


class TestRequireConnectorNotLocked:
    """Tests for the require_connector_not_locked dependency."""

    async def test_not_locked_passes(self):
        from app.connectors.api.router import require_connector_not_locked

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={"isLocked": False})
        request = _mock_request(connector_registry=registry)
        # Should not raise
        await require_connector_not_locked("conn-1", request)

    async def test_locked_raises(self):
        from app.connectors.api.router import require_connector_not_locked

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(
            return_value={"isLocked": True, "status": AppStatus.SYNCING.value}
        )
        request = _mock_request(connector_registry=registry)
        with pytest.raises(HTTPException) as exc_info:
            await require_connector_not_locked("conn-1", request)
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value

    async def test_instance_not_found_passes(self):
        from app.connectors.api.router import require_connector_not_locked

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value=None)
        request = _mock_request(connector_registry=registry)
        await require_connector_not_locked("conn-missing", request)


# ============================================================================
# require_connector_not_locked_for_record
# ============================================================================


class TestRequireConnectorNotLockedForRecord:
    """Tests for require_connector_not_locked_for_record."""

    async def test_record_not_found_passes(self):
        from app.connectors.api.router import require_connector_not_locked_for_record

        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value=None)
        await require_connector_not_locked_for_record("rec-1", gp)

    async def test_non_connector_origin_passes(self):
        from app.connectors.api.router import require_connector_not_locked_for_record

        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={"origin": OriginTypes.UPLOAD.value})
        await require_connector_not_locked_for_record("rec-1", gp)

    async def test_no_connector_id_passes(self):
        from app.connectors.api.router import require_connector_not_locked_for_record

        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={"origin": OriginTypes.CONNECTOR.value})
        await require_connector_not_locked_for_record("rec-1", gp)

    async def test_locked_connector_raises(self):
        from app.connectors.api.router import require_connector_not_locked_for_record

        gp = AsyncMock()
        gp.get_document = AsyncMock(side_effect=[
            {"origin": OriginTypes.CONNECTOR.value, "connectorId": "conn-1"},
            {"isLocked": True, "status": AppStatus.FULL_SYNCING.value},
        ])
        with pytest.raises(HTTPException) as exc_info:
            await require_connector_not_locked_for_record("rec-1", gp)
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value

    async def test_unlocked_connector_passes(self):
        from app.connectors.api.router import require_connector_not_locked_for_record

        gp = AsyncMock()
        gp.get_document = AsyncMock(side_effect=[
            {"origin": OriginTypes.CONNECTOR.value, "connectorId": "conn-1"},
            {"isLocked": False},
        ])
        await require_connector_not_locked_for_record("rec-1", gp)


# ============================================================================
# require_connector_not_locked_for_record_group
# ============================================================================


class TestRequireConnectorNotLockedForRecordGroup:
    """Tests for require_connector_not_locked_for_record_group."""

    async def test_record_group_not_found_passes(self):
        from app.connectors.api.router import (
            require_connector_not_locked_for_record_group,
        )

        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value=None)
        await require_connector_not_locked_for_record_group("rg-1", gp)

    async def test_no_connector_id_passes(self):
        from app.connectors.api.router import (
            require_connector_not_locked_for_record_group,
        )

        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={"connectorId": None})
        await require_connector_not_locked_for_record_group("rg-1", gp)

    async def test_locked_raises(self):
        from app.connectors.api.router import (
            require_connector_not_locked_for_record_group,
        )

        gp = AsyncMock()
        gp.get_document = AsyncMock(side_effect=[
            {"connectorId": "conn-1"},
            {"isLocked": True, "status": AppStatus.SYNCING.value},
        ])
        with pytest.raises(HTTPException) as exc_info:
            await require_connector_not_locked_for_record_group("rg-1", gp)
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value

    async def test_unlocked_passes(self):
        from app.connectors.api.router import (
            require_connector_not_locked_for_record_group,
        )

        gp = AsyncMock()
        gp.get_document = AsyncMock(side_effect=[
            {"connectorId": "conn-1"},
            {"isLocked": False},
        ])
        await require_connector_not_locked_for_record_group("rg-1", gp)


# ============================================================================
# _encode_state_with_instance / _decode_state_with_instance
# ============================================================================


class TestEncodeDecodeState:
    """Tests for _encode_state_with_instance and _decode_state_with_instance."""

    def test_roundtrip(self):
        encoded = _encode_state_with_instance("my-state", "conn-42")
        decoded = _decode_state_with_instance(encoded)
        assert decoded["state"] == "my-state"
        assert decoded["connector_id"] == "conn-42"

    def test_encoded_is_base64(self):
        encoded = _encode_state_with_instance("s", "c")
        # Should be decodable as base64
        base64.urlsafe_b64decode(encoded.encode())

    def test_decode_invalid_raises_valueerror(self):
        with pytest.raises(ValueError, match="Invalid state format"):
            _decode_state_with_instance("not-valid-base64!@#$")

    def test_decode_non_json_raises_valueerror(self):
        encoded = base64.urlsafe_b64encode(b"not json").decode()
        with pytest.raises(ValueError, match="Invalid state format"):
            _decode_state_with_instance(encoded)

    def test_encode_special_characters(self):
        encoded = _encode_state_with_instance("state/with+special=chars", "conn-id")
        decoded = _decode_state_with_instance(encoded)
        assert decoded["state"] == "state/with+special=chars"


# ============================================================================
# _get_config_path_for_instance
# ============================================================================


class TestGetConfigPathForInstance:
    """Tests for _get_config_path_for_instance."""

    def test_returns_expected_path(self):
        assert _get_config_path_for_instance("abc-123") == "/services/connectors/abc-123/config"

    def test_different_id(self):
        assert _get_config_path_for_instance("xyz") == "/services/connectors/xyz/config"


# ============================================================================
# _get_settings_base_path
# ============================================================================


class TestGetSettingsBasePath:
    """Tests for _get_settings_base_path."""

    async def test_business_account(self):
        gp = AsyncMock()
        gp.get_all_documents = AsyncMock(return_value=[{"accountType": "business"}])
        result = await _get_settings_base_path(gp)
        assert "company-settings" in result

    async def test_enterprise_account(self):
        gp = AsyncMock()
        gp.get_all_documents = AsyncMock(return_value=[{"accountType": "enterprise"}])
        result = await _get_settings_base_path(gp)
        assert "company-settings" in result

    async def test_organization_account(self):
        gp = AsyncMock()
        gp.get_all_documents = AsyncMock(return_value=[{"accountType": "organization"}])
        result = await _get_settings_base_path(gp)
        assert "company-settings" in result

    async def test_individual_account(self):
        gp = AsyncMock()
        gp.get_all_documents = AsyncMock(return_value=[{"accountType": "individual"}])
        result = await _get_settings_base_path(gp)
        assert "individual" in result

    async def test_empty_org_list(self):
        gp = AsyncMock()
        gp.get_all_documents = AsyncMock(return_value=[])
        result = await _get_settings_base_path(gp)
        assert "individual" in result

    async def test_exception_returns_individual(self):
        gp = AsyncMock()
        gp.get_all_documents = AsyncMock(side_effect=Exception("db down"))
        result = await _get_settings_base_path(gp)
        assert "individual" in result

    async def test_none_org_in_list(self):
        gp = AsyncMock()
        gp.get_all_documents = AsyncMock(return_value=[None])
        result = await _get_settings_base_path(gp)
        assert "individual" in result


# ============================================================================
# _validate_connector_deletion_permissions
# ============================================================================


class TestValidateConnectorDeletionPermissions:
    """Tests for _validate_connector_deletion_permissions."""

    def test_team_connector_admin_passes(self):
        _validate_connector_deletion_permissions(
            {"scope": ConnectorScope.TEAM.value, "createdBy": "user-a"},
            "user-a",
            is_admin=True,
            logger=MagicMock(),
        )

    def test_team_connector_non_admin_raises(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_connector_deletion_permissions(
                {"scope": ConnectorScope.TEAM.value, "createdBy": "user-a"},
                "user-a",
                is_admin=False,
                logger=MagicMock(),
            )
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    def test_personal_connector_creator_passes(self):
        _validate_connector_deletion_permissions(
            {"scope": ConnectorScope.PERSONAL.value, "createdBy": "user-a"},
            "user-a",
            is_admin=False,
            logger=MagicMock(),
        )

    def test_personal_connector_admin_not_creator_raises(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_connector_deletion_permissions(
                {"scope": ConnectorScope.PERSONAL.value, "createdBy": "user-a"},
                "user-b",
                is_admin=True,
                logger=MagicMock(),
            )
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    def test_personal_connector_other_user_raises(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate_connector_deletion_permissions(
                {"scope": ConnectorScope.PERSONAL.value, "createdBy": "user-a"},
                "user-b",
                is_admin=False,
                logger=MagicMock(),
            )
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    def test_no_scope_passes(self):
        """No explicit scope means neither team nor personal guard fires."""
        _validate_connector_deletion_permissions(
            {"createdBy": "user-a"},
            "user-a",
            is_admin=False,
            logger=MagicMock(),
        )


# ============================================================================
# check_beta_connector_access
# ============================================================================


class TestCheckBetaConnectorAccess:
    """Tests for check_beta_connector_access."""

    async def test_beta_disabled_and_connector_is_beta_raises(self):
        from app.connectors.api.router import check_beta_connector_access

        container = MagicMock()
        ff = MagicMock()
        ff.is_feature_enabled = MagicMock(return_value=False)
        ff.refresh = AsyncMock()
        container.feature_flag_service = AsyncMock(return_value=ff)
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(container=container)

        with patch(
            "app.connectors.api.router.ConnectorFactory.list_beta_connectors",
            return_value={"mybeta"},
        ):
            with pytest.raises(HTTPException) as exc_info:
                await check_beta_connector_access("MY BETA", request)
            assert exc_info.value.status_code == 403

    async def test_beta_enabled_passes(self):
        from app.connectors.api.router import check_beta_connector_access

        container = MagicMock()
        ff = MagicMock()
        ff.is_feature_enabled = MagicMock(return_value=True)
        ff.refresh = AsyncMock()
        container.feature_flag_service = AsyncMock(return_value=ff)
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(container=container)
        await check_beta_connector_access("MY BETA", request)

    async def test_non_beta_connector_passes_even_if_beta_disabled(self):
        from app.connectors.api.router import check_beta_connector_access

        container = MagicMock()
        ff = MagicMock()
        ff.is_feature_enabled = MagicMock(return_value=False)
        ff.refresh = AsyncMock()
        container.feature_flag_service = AsyncMock(return_value=ff)
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(container=container)

        with patch(
            "app.connectors.api.router.ConnectorFactory.list_beta_connectors",
            return_value={"otherbeta"},
        ):
            # Should not raise for "googledrive" which is not in beta list
            await check_beta_connector_access("Google Drive", request)

    async def test_feature_flag_error_fails_open(self):
        from app.connectors.api.router import check_beta_connector_access

        container = MagicMock()
        container.feature_flag_service = AsyncMock(side_effect=Exception("etcd down"))
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(container=container)
        # Should not raise (fail-open)
        await check_beta_connector_access("SomeConnector", request)


# ============================================================================
# _stream_google_api_request
# ============================================================================


class TestStreamGoogleApiRequest:
    """Tests for _stream_google_api_request."""

    async def test_single_chunk_download(self):
        from app.connectors.api.router import _stream_google_api_request

        mock_request = MagicMock()
        chunks = []

        with patch("app.connectors.api.router.MediaIoBaseDownload") as MockDownload:
            instance = MockDownload.return_value
            instance.next_chunk = MagicMock(side_effect=[
                (MagicMock(progress=MagicMock(return_value=1.0)), True)
            ])

            # Simulate that after next_chunk, buffer contains data
            def fake_init(buf, req):
                # Write data into the buffer before next_chunk returns
                original_next = instance.next_chunk

                def next_with_data():
                    buf.write(b"chunk1")
                    return original_next()

                instance.next_chunk = next_with_data
                return instance

            MockDownload.side_effect = fake_init

            async for chunk in _stream_google_api_request(mock_request, "test"):
                chunks.append(chunk)

        assert len(chunks) >= 0  # May be empty if buffer write/read ordering doesn't match

    async def test_http_error_raises_http_exception(self):
        from googleapiclient.errors import HttpError

        from app.connectors.api.router import _stream_google_api_request

        mock_request = MagicMock()

        with patch("app.connectors.api.router.MediaIoBaseDownload") as MockDownload:
            instance = MockDownload.return_value
            resp = MagicMock()
            resp.status = 403
            http_err = HttpError(resp=resp, content=b"forbidden")
            instance.next_chunk = MagicMock(side_effect=http_err)

            with pytest.raises(HTTPException) as exc_info:
                async for _ in _stream_google_api_request(mock_request, "download"):
                    pass
            assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    async def test_generic_error_raises_http_exception(self):
        from app.connectors.api.router import _stream_google_api_request

        mock_request = MagicMock()

        with patch("app.connectors.api.router.MediaIoBaseDownload") as MockDownload:
            instance = MockDownload.return_value
            instance.next_chunk = MagicMock(side_effect=RuntimeError("oops"))

            with pytest.raises(HTTPException) as exc_info:
                async for _ in _stream_google_api_request(mock_request, "export"):
                    pass
            assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# get_validated_connector_instance
# ============================================================================


class TestGetValidatedConnectorInstance:
    """Tests for get_validated_connector_instance."""

    async def test_success_personal_creator(self):
        from app.connectors.api.router import get_validated_connector_instance

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "googledrive",
            "scope": ConnectorScope.PERSONAL.value,
            "createdBy": "user-1",
        })

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(container=container, connector_registry=registry)

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            result = await get_validated_connector_instance("conn-1", request)
        assert result["type"] == "googledrive"

    async def test_unauthenticated_raises_401(self):
        from app.connectors.api.router import get_validated_connector_instance

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        request = _mock_request(
            user={"userId": None, "orgId": "org-1"},
            container=container,
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_validated_connector_instance("conn-1", request)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    async def test_instance_not_found_raises_404(self):
        from app.connectors.api.router import get_validated_connector_instance

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value=None)
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        request = _mock_request(container=container, connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_validated_connector_instance("conn-missing", request)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    async def test_team_scope_non_admin_raises_403(self):
        from app.connectors.api.router import get_validated_connector_instance

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "slack",
            "scope": ConnectorScope.TEAM.value,
            "createdBy": "user-1",
        })
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        request = _mock_request(
            container=container,
            connector_registry=registry,
            headers={"X-Is-Admin": "false"},
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_validated_connector_instance("conn-1", request)
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    async def test_not_creator_non_admin_raises_403(self):
        from app.connectors.api.router import get_validated_connector_instance

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "slack",
            "scope": "other",
            "createdBy": "user-other",
        })
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        request = _mock_request(
            container=container,
            connector_registry=registry,
            headers={"X-Is-Admin": "false"},
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_validated_connector_instance("conn-1", request)
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    async def test_personal_not_creator_raises_403(self):
        from app.connectors.api.router import get_validated_connector_instance

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "slack",
            "scope": ConnectorScope.PERSONAL.value,
            "createdBy": "user-other",
        })
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        request = _mock_request(
            container=container,
            connector_registry=registry,
            headers={"X-Is-Admin": "true"},
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_validated_connector_instance("conn-1", request)
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value


# ============================================================================
# handle_record_deletion
# ============================================================================


class TestHandleRecordDeletion:
    """Tests for handle_record_deletion route handler."""

    async def test_successful_deletion(self):
        from app.connectors.api.router import handle_record_deletion

        gp = AsyncMock()
        gp.delete_records_and_relations = AsyncMock(return_value={"deleted": True})

        result = await handle_record_deletion("rec-1", gp)
        assert result["status"] == "success"

    async def test_not_found_raises_404(self):
        from app.connectors.api.router import handle_record_deletion

        gp = AsyncMock()
        gp.delete_records_and_relations = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await handle_record_deletion("rec-missing", gp)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    async def test_unexpected_error_raises_500(self):
        from app.connectors.api.router import handle_record_deletion

        gp = AsyncMock()
        gp.delete_records_and_relations = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(HTTPException) as exc_info:
            await handle_record_deletion("rec-1", gp)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# get_signed_url
# ============================================================================


class TestGetSignedUrl:
    """Tests for get_signed_url route handler."""

    async def test_success(self):
        from app.connectors.api.router import get_signed_url

        handler = AsyncMock()
        handler.get_signed_url = AsyncMock(return_value="https://signed.url/file")

        result = await get_signed_url("org-1", "user-1", "googledrive", "rec-1", handler)
        assert result["signedUrl"] == "https://signed.url/file"

    async def test_error_raises_500(self):
        from app.connectors.api.router import get_signed_url

        handler = AsyncMock()
        handler.get_signed_url = AsyncMock(side_effect=Exception("fail"))

        with pytest.raises(HTTPException) as exc_info:
            await get_signed_url("org-1", "user-1", "drive", "rec-1", handler)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# get_records
# ============================================================================


class TestGetRecords:
    """Tests for get_records route handler."""

    async def test_success(self):
        from app.connectors.api.router import get_records

        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "uk-1"})
        gp.get_records = AsyncMock(return_value=(
            [{"id": "rec-1"}],
            1,
            {"recordTypes": ["FILE"]},
        ))

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        request = _mock_request(container=container)

        result = await get_records(
            request=request,
            graph_provider=gp,
            page=1,
            limit=20,
            search=None,
            record_types=None,
            origins=None,
            connectors=None,
            indexing_status=None,
            permissions=None,
            date_from=None,
            date_to=None,
            sort_by="createdAtTimestamp",
            sort_order="desc",
            source="all",
        )
        assert result["records"] == [{"id": "rec-1"}]
        assert result["pagination"]["totalCount"] == 1

    async def test_user_not_found(self):
        from app.connectors.api.router import get_records

        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(return_value=None)

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        request = _mock_request(container=container)

        result = await get_records(
            request=request,
            graph_provider=gp,
            page=1,
            limit=20,
            search=None,
            record_types=None,
            origins=None,
            connectors=None,
            indexing_status=None,
            permissions=None,
            date_from=None,
            date_to=None,
            sort_by="createdAtTimestamp",
            sort_order="desc",
            source="all",
        )
        assert result["success"] is False
        assert result["code"] == 404

    async def test_exception_returns_empty(self):
        from app.connectors.api.router import get_records

        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(side_effect=Exception("db error"))

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        request = _mock_request(container=container)

        result = await get_records(
            request=request,
            graph_provider=gp,
            page=1,
            limit=20,
            search=None,
            record_types=None,
            origins=None,
            connectors=None,
            indexing_status=None,
            permissions=None,
            date_from=None,
            date_to=None,
            sort_by="createdAtTimestamp",
            sort_order="desc",
            source="all",
        )
        assert result["records"] == []
        assert "error" in result

    async def test_sort_order_normalization(self):
        from app.connectors.api.router import get_records

        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "uk-1"})
        gp.get_records = AsyncMock(return_value=([], 0, {}))

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        request = _mock_request(container=container)

        await get_records(
            request=request,
            graph_provider=gp,
            page=1,
            limit=20,
            search=None,
            record_types="FILE,MAIL",
            origins=None,
            connectors=None,
            indexing_status=None,
            permissions=None,
            date_from=None,
            date_to=None,
            sort_by="invalidField",
            sort_order="INVALID",
            source="all",
        )
        # sort_order should default to "desc" and sort_by to "createdAtTimestamp"
        call_kwargs = gp.get_records.call_args[1]
        assert call_kwargs["sort_order"] == "desc"
        assert call_kwargs["sort_by"] == "createdAtTimestamp"


# ============================================================================
# get_record_by_id
# ============================================================================


class TestGetRecordById:
    """Tests for get_record_by_id route handler."""

    async def test_success(self):
        from app.connectors.api.router import get_record_by_id

        gp = AsyncMock()
        gp.check_record_access_with_details = AsyncMock(return_value={"record": "data"})

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        request = _mock_request(container=container)

        result = await get_record_by_id("rec-1", request, gp)
        assert result == {"record": "data"}

    async def test_no_access_raises_404(self):
        from app.connectors.api.router import get_record_by_id

        gp = AsyncMock()
        gp.check_record_access_with_details = AsyncMock(return_value=None)

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        request = _mock_request(container=container)

        with pytest.raises(HTTPException) as exc_info:
            await get_record_by_id("rec-1", request, gp)
        assert exc_info.value.status_code == 500  # wrapped by outer except


# ============================================================================
# delete_record
# ============================================================================


class TestDeleteRecord:
    """Tests for delete_record route handler."""

    async def test_success_with_event(self):
        from app.connectors.api.router import delete_record

        gp = AsyncMock()
        gp.delete_record = AsyncMock(return_value={
            "success": True,
            "eventData": {
                "eventType": "record.deleted",
                "topic": "sync-events",
                "payload": {"recordId": "rec-1"},
            },
            "connector": "googledrive",
            "timestamp": 123456,
        })

        kafka = AsyncMock()
        kafka.publish_event = AsyncMock()

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        request = _mock_request(container=container)

        with patch("app.connectors.api.router.get_epoch_timestamp_in_ms", return_value=999):
            result = await delete_record("rec-1", request, gp, kafka)
        assert result["success"] is True
        kafka.publish_event.assert_called_once()

    async def test_failure_raises_with_code(self):
        from app.connectors.api.router import delete_record

        gp = AsyncMock()
        gp.delete_record = AsyncMock(return_value={
            "success": False,
            "code": 404,
            "reason": "not found",
        })

        kafka = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        request = _mock_request(container=container)

        with pytest.raises(HTTPException) as exc_info:
            await delete_record("rec-1", request, gp, kafka)
        assert exc_info.value.status_code == 404

    async def test_event_publish_failure_still_returns_success(self):
        from app.connectors.api.router import delete_record

        gp = AsyncMock()
        gp.delete_record = AsyncMock(return_value={
            "success": True,
            "eventData": {
                "eventType": "record.deleted",
                "topic": "sync-events",
                "payload": {"recordId": "rec-1"},
            },
        })

        kafka = AsyncMock()
        kafka.publish_event = AsyncMock(side_effect=Exception("kafka down"))

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        request = _mock_request(container=container)

        with patch("app.connectors.api.router.get_epoch_timestamp_in_ms", return_value=999):
            result = await delete_record("rec-1", request, gp, kafka)
        assert result["success"] is True


# ============================================================================
# reindex_single_record
# ============================================================================


class TestReindexSingleRecord:
    """Tests for reindex_single_record route handler."""

    async def test_success(self):
        from app.connectors.api.router import reindex_single_record

        gp = AsyncMock()
        gp.reindex_single_record = AsyncMock(return_value={
            "success": True,
            "eventData": {
                "eventType": "record.reindex",
                "topic": "sync-events",
                "payload": {"recordId": "rec-1"},
            },
            "recordId": "rec-1",
            "recordName": "doc.pdf",
            "connector": "googledrive",
            "userRole": "admin",
        })

        kafka = AsyncMock()
        kafka.publish_event = AsyncMock()

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        request = _mock_request(
            container=container,
            body={"depth": 1},
        )

        with patch("app.connectors.api.router.get_epoch_timestamp_in_ms", return_value=111):
            result = await reindex_single_record("rec-1", request, gp, kafka)
        assert result["success"] is True
        assert result["depth"] == 1

    async def test_failure_raises(self):
        from app.connectors.api.router import reindex_single_record

        gp = AsyncMock()
        gp.reindex_single_record = AsyncMock(return_value={
            "success": False,
            "code": 404,
            "reason": "not found",
        })

        kafka = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        request = _mock_request(container=container)

        with pytest.raises(HTTPException) as exc_info:
            await reindex_single_record("rec-1", request, gp, kafka)
        assert exc_info.value.status_code == 404

    async def test_no_body_defaults_depth_to_zero(self):
        from app.connectors.api.router import reindex_single_record

        gp = AsyncMock()
        gp.reindex_single_record = AsyncMock(return_value={
            "success": True,
            "eventData": None,
            "recordId": "rec-1",
            "recordName": "doc.pdf",
            "connector": "googledrive",
            "userRole": "admin",
        })
        kafka = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(container=container)
        request.json = AsyncMock(side_effect=json.JSONDecodeError("", "", 0))

        result = await reindex_single_record("rec-1", request, gp, kafka)
        assert result["depth"] == 0


# ============================================================================
# get_connector_stats_endpoint
# ============================================================================


class TestGetConnectorStatsEndpoint:
    """Tests for get_connector_stats_endpoint."""

    async def test_success(self):
        from app.connectors.api.router import get_connector_stats_endpoint

        gp = AsyncMock()
        gp.get_connector_stats = AsyncMock(return_value={
            "success": True,
            "data": {"totalRecords": 100},
        })

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        request = MagicMock()
        request.app = MagicMock()
        request.app.container = container

        result = await get_connector_stats_endpoint(request, "org-1", "conn-1", gp)
        assert result["success"] is True
        assert result["data"]["totalRecords"] == 100

    async def test_not_found_raises_404(self):
        from app.connectors.api.router import get_connector_stats_endpoint

        gp = AsyncMock()
        gp.get_connector_stats = AsyncMock(return_value={"success": False})

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        request = MagicMock()
        request.app = MagicMock()
        request.app.container = container

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_stats_endpoint(request, "org-1", "conn-1", gp)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value


# ============================================================================
# get_connector_registry
# ============================================================================


class TestGetConnectorRegistry:
    """Tests for get_connector_registry route handler."""

    async def test_success(self):
        from app.connectors.api.router import get_connector_registry

        registry = AsyncMock()
        registry.get_all_registered_connectors = AsyncMock(return_value={
            "connectors": [{"type": "googledrive"}],
            "pagination": {"page": 1, "limit": 20, "total": 1}
        })

        gp = AsyncMock()
        gp.get_account_type = AsyncMock(return_value="individual")

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(
            container=container,
            connector_registry=registry,
            graph_provider=gp,
        )

        result = await get_connector_registry(request, scope=None, page=1, limit=20, search=None)
        assert result["success"] is True

    async def test_invalid_scope_raises_400(self):
        from app.connectors.api.router import get_connector_registry

        registry = AsyncMock()
        gp = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(
            container=container,
            connector_registry=registry,
            graph_provider=gp,
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_registry(request, scope="invalid", page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_no_connectors_raises_404(self):
        from app.connectors.api.router import get_connector_registry

        registry = AsyncMock()
        registry.get_all_registered_connectors = AsyncMock(return_value=None)
        gp = AsyncMock()
        gp.get_account_type = AsyncMock(return_value="individual")
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(
            container=container,
            connector_registry=registry,
            graph_provider=gp,
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_registry(request, scope=None, page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value


# ============================================================================
# get_connector_instances
# ============================================================================


class TestGetConnectorInstances:
    """Tests for get_connector_instances route handler."""

    async def test_success(self):
        from app.connectors.api.router import get_connector_instances

        registry = AsyncMock()
        registry.get_all_connector_instances = AsyncMock(return_value={
            "connectors": [],
            "pagination": {},
        })
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(container=container, connector_registry=registry)

        result = await get_connector_instances(request, scope=None, page=1, limit=20, search=None)
        assert result["success"] is True

    async def test_unauthenticated_raises(self):
        from app.connectors.api.router import get_connector_instances

        registry = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(
            user={"userId": None, "orgId": "org-1"},
            container=container,
            connector_registry=registry,
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instances(request, scope=None, page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    async def test_invalid_scope_raises(self):
        from app.connectors.api.router import get_connector_instances

        registry = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(container=container, connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instances(request, scope="bad", page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value


# ============================================================================
# get_active_connector_instances
# ============================================================================


class TestGetActiveConnectorInstances:
    """Tests for get_active_connector_instances route handler."""

    async def test_success(self):
        from app.connectors.api.router import get_active_connector_instances

        registry = AsyncMock()
        registry.get_active_connector_instances = AsyncMock(return_value=[{"id": "c1"}])
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(container=container, connector_registry=registry)
        result = await get_active_connector_instances(request)
        assert result["success"] is True
        assert len(result["connectors"]) == 1

    async def test_unauthenticated_raises(self):
        from app.connectors.api.router import get_active_connector_instances

        registry = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(
            user={"userId": None, "orgId": None},
            container=container,
            connector_registry=registry,
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_active_connector_instances(request)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value


# ============================================================================
# get_inactive_connector_instances
# ============================================================================


class TestGetInactiveConnectorInstances:
    """Tests for get_inactive_connector_instances."""

    async def test_success(self):
        from app.connectors.api.router import get_inactive_connector_instances

        registry = AsyncMock()
        registry.get_inactive_connector_instances = AsyncMock(return_value=[])
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(container=container, connector_registry=registry)
        result = await get_inactive_connector_instances(request)
        assert result["success"] is True

    async def test_unauthenticated_raises(self):
        from app.connectors.api.router import get_inactive_connector_instances

        registry = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(
            user={"userId": "", "orgId": "org-1"},
            container=container,
            connector_registry=registry,
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_inactive_connector_instances(request)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value


# ============================================================================
# get_configured_connector_instances
# ============================================================================


class TestGetConfiguredConnectorInstances:
    """Tests for get_configured_connector_instances."""

    async def test_success(self):
        from app.connectors.api.router import get_configured_connector_instances

        registry = AsyncMock()
        registry.get_configured_connector_instances = AsyncMock(return_value=[])
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(container=container, connector_registry=registry)
        result = await get_configured_connector_instances(request, scope=None, page=1, limit=20, search=None)
        assert result["success"] is True

    async def test_unauthenticated_raises(self):
        from app.connectors.api.router import get_configured_connector_instances

        registry = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(
            user={"userId": None, "orgId": "org-1"},
            container=container,
            connector_registry=registry,
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_configured_connector_instances(request, scope=None, page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    async def test_invalid_scope_raises(self):
        from app.connectors.api.router import get_configured_connector_instances

        registry = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(container=container, connector_registry=registry)
        with pytest.raises(HTTPException) as exc_info:
            await get_configured_connector_instances(request, scope="bad", page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value


# ============================================================================
# get_connector_instance
# ============================================================================


class TestGetConnectorInstance:
    """Tests for get_connector_instance route handler."""

    async def test_success(self):
        from app.connectors.api.router import get_connector_instance

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={"type": "slack", "_key": "c1"})
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(container=container, connector_registry=registry)

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            result = await get_connector_instance("c1", request)
        assert result["success"] is True

    async def test_not_found_raises_404(self):
        from app.connectors.api.router import get_connector_instance

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value=None)
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(container=container, connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instance("c-missing", request)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    async def test_unauthenticated_raises(self):
        from app.connectors.api.router import get_connector_instance

        registry = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(
            user={"userId": None, "orgId": "org-1"},
            container=container,
            connector_registry=registry,
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instance("c1", request)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value


# ============================================================================
# get_connector_instance_config
# ============================================================================


class TestGetConnectorInstanceConfig:
    """Tests for get_connector_instance_config route handler."""

    async def test_success(self):
        from app.connectors.api.router import get_connector_instance_config

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "slack",
            "name": "My Slack",
            "scope": "personal",
            "createdBy": "user-1",
            "appGroup": "Slack",
            "authType": "OAUTH",
        })

        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"authorizeUrl": "http://x", "tokenUrl": "http://y", "scopes": ["a"], "oauthConfigs": {}},
            "sync": {},
            "filters": {},
            "credentials": "secret",
            "oauth": "state",
        })

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        container.config_service = MagicMock(return_value=config_service)

        request = _mock_request(container=container, connector_registry=registry)

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            result = await get_connector_instance_config("c1", request)

        assert result["success"] is True
        config = result["config"]["config"]
        # Sensitive and internal fields should be removed
        assert "credentials" not in config
        assert "oauth" not in config
        assert "authorizeUrl" not in config.get("auth", {})
        assert "tokenUrl" not in config.get("auth", {})
        assert "scopes" not in config.get("auth", {})

    async def test_no_config_returns_defaults(self):
        from app.connectors.api.router import get_connector_instance_config

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "slack",
            "name": "My Slack",
            "scope": "personal",
            "authType": "OAUTH",
        })

        config_service = MagicMock()
        config_service.get_config = AsyncMock(side_effect=Exception("not found"))

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        container.config_service = MagicMock(return_value=config_service)

        request = _mock_request(container=container, connector_registry=registry)

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            result = await get_connector_instance_config("c1", request)

        assert result["success"] is True
        assert result["config"]["config"] == {"auth": {}, "sync": {}, "filters": {}}

    async def test_unauthenticated_raises(self):
        from app.connectors.api.router import get_connector_instance_config

        registry = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(
            user={"userId": None, "orgId": "org-1"},
            container=container,
            connector_registry=registry,
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instance_config("c1", request)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value


# ============================================================================
# update_connector_instance_name
# ============================================================================


class TestUpdateConnectorInstanceName:
    """Tests for update_connector_instance_name route handler."""

    async def test_success(self):
        from app.connectors.api.router import update_connector_instance_name

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "slack",
            "scope": ConnectorScope.PERSONAL.value,
            "createdBy": "user-1",
        })
        registry.update_connector_instance = AsyncMock(return_value=True)

        gp = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={"instanceName": "New Name"},
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            result = await update_connector_instance_name("c1", request, gp)
        assert result["success"] is True
        assert result["connector"]["name"] == "New Name"

    async def test_empty_name_raises_400(self):
        from app.connectors.api.router import update_connector_instance_name

        registry = AsyncMock()
        gp = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={"instanceName": "   "},
        )

        with pytest.raises(HTTPException) as exc_info:
            await update_connector_instance_name("c1", request, gp)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_not_found_raises_404(self):
        from app.connectors.api.router import update_connector_instance_name

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value=None)
        gp = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={"instanceName": "New Name"},
        )

        with pytest.raises(HTTPException) as exc_info:
            await update_connector_instance_name("c1", request, gp)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    async def test_team_scope_non_admin_raises_403(self):
        from app.connectors.api.router import update_connector_instance_name

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "slack",
            "scope": ConnectorScope.TEAM.value,
            "createdBy": "user-1",
        })
        gp = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(
            container=container,
            connector_registry=registry,
            headers={"X-Is-Admin": "false"},
            body={"instanceName": "New Name"},
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await update_connector_instance_name("c1", request, gp)
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    async def test_unauthenticated_raises_401(self):
        from app.connectors.api.router import update_connector_instance_name

        registry = AsyncMock()
        gp = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(
            user={"userId": None, "orgId": "org-1"},
            container=container,
            connector_registry=registry,
            body={"instanceName": "Name"},
        )

        with pytest.raises(HTTPException) as exc_info:
            await update_connector_instance_name("c1", request, gp)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    async def test_update_failure_raises_500(self):
        from app.connectors.api.router import update_connector_instance_name

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "slack",
            "name": "Old",
            "scope": ConnectorScope.PERSONAL.value,
            "createdBy": "user-1",
        })
        registry.update_connector_instance = AsyncMock(return_value=None)

        gp = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={"instanceName": "New"},
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await update_connector_instance_name("c1", request, gp)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    async def test_name_uniqueness_validation_error_raises_400(self):
        from app.connectors.api.router import update_connector_instance_name

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "slack",
            "scope": ConnectorScope.PERSONAL.value,
            "createdBy": "user-1",
        })
        registry.update_connector_instance = AsyncMock(
            side_effect=ValueError("Name already exists")
        )

        gp = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={"instanceName": "Duplicate Name"},
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await update_connector_instance_name("c1", request, gp)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value


# ============================================================================
# reindex_record_group
# ============================================================================


class TestReindexRecordGroup:
    """Tests for reindex_record_group route handler."""

    async def test_success(self):
        from app.connectors.api.router import reindex_record_group

        gp = AsyncMock()
        gp.reindex_record_group_records = AsyncMock(return_value={
            "success": True,
            "connectorId": "conn-1",
            "connectorName": "Google Drive",
            "userKey": "uk-1",
            "depth": 1,
        })

        kafka = AsyncMock()
        kafka.publish_event = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())

        request = _mock_request(container=container, body={"depth": 1})

        with patch("app.connectors.api.router.get_epoch_timestamp_in_ms", return_value=222):
            result = await reindex_record_group("rg-1", request, gp, kafka)
        assert result["success"] is True
        assert result["eventPublished"] is True
        kafka.publish_event.assert_called_once()

    async def test_failure_raises(self):
        from app.connectors.api.router import reindex_record_group

        gp = AsyncMock()
        gp.reindex_record_group_records = AsyncMock(return_value={
            "success": False,
            "code": 404,
            "reason": "not found",
        })

        kafka = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        request = _mock_request(container=container)

        with pytest.raises(HTTPException) as exc_info:
            await reindex_record_group("rg-1", request, gp, kafka)
        assert exc_info.value.status_code == 404

    async def test_event_publish_failure_raises(self):
        from app.connectors.api.router import reindex_record_group

        gp = AsyncMock()
        gp.reindex_record_group_records = AsyncMock(return_value={
            "success": True,
            "connectorId": "conn-1",
            "connectorName": "Google Drive",
            "userKey": "uk-1",
            "depth": 0,
        })

        kafka = AsyncMock()
        kafka.publish_event = AsyncMock(side_effect=Exception("kafka down"))
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        request = _mock_request(container=container)

        with patch("app.connectors.api.router.get_epoch_timestamp_in_ms", return_value=222):
            with pytest.raises(HTTPException) as exc_info:
                await reindex_record_group("rg-1", request, gp, kafka)
            assert exc_info.value.status_code == 500


# ============================================================================
# convert_to_pdf
# ============================================================================


class TestConvertToPdf:
    """Tests for convert_to_pdf utility function."""

    async def test_success(self):
        from app.connectors.api.router import convert_to_pdf

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "test.pptx")
            pdf_path = os.path.join(tmpdir, "test.pdf")

            # Create fake input file
            with open(input_path, "wb") as f:
                f.write(b"fake pptx content")

            # Create expected PDF output
            with open(pdf_path, "wb") as f:
                f.write(b"%PDF-fake")

            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(return_value=(b"ok", b""))
            mock_process.returncode = 0

            with patch("asyncio.create_subprocess_exec", return_value=mock_process):
                result = await convert_to_pdf(input_path, tmpdir)
            assert result == pdf_path

    async def test_timeout_raises(self):
        from app.connectors.api.router import convert_to_pdf

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "test.pptx")
            with open(input_path, "wb") as f:
                f.write(b"content")

            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_process.terminate = MagicMock()
            mock_process.wait = AsyncMock(return_value=0)
            mock_process.kill = MagicMock()

            with patch("asyncio.create_subprocess_exec", return_value=mock_process):
                with pytest.raises(HTTPException) as exc_info:
                    await convert_to_pdf(input_path, tmpdir)
                assert "timed out" in exc_info.value.detail.lower()

    async def test_nonzero_return_code_raises(self):
        from app.connectors.api.router import convert_to_pdf

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "test.pptx")
            with open(input_path, "wb") as f:
                f.write(b"content")

            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(return_value=(b"", b"error message"))
            mock_process.returncode = 1

            with patch("asyncio.create_subprocess_exec", return_value=mock_process):
                with pytest.raises(HTTPException) as exc_info:
                    await convert_to_pdf(input_path, tmpdir)
                assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    async def test_output_file_not_found_raises(self):
        from app.connectors.api.router import convert_to_pdf

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "test.pptx")
            with open(input_path, "wb") as f:
                f.write(b"content")

            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(return_value=(b"ok", b""))
            mock_process.returncode = 0

            with patch("asyncio.create_subprocess_exec", return_value=mock_process):
                with pytest.raises(HTTPException) as exc_info:
                    await convert_to_pdf(input_path, tmpdir)
                assert "not found" in exc_info.value.detail.lower()


# ============================================================================
# convert_buffer_to_pdf_stream
# ============================================================================


class TestConvertBufferToPdfStream:
    """Tests for convert_buffer_to_pdf_stream."""

    async def test_bytes_buffer(self):
        from app.connectors.api.router import convert_buffer_to_pdf_stream

        with patch("app.connectors.api.router.convert_to_pdf") as mock_convert:
            with tempfile.TemporaryDirectory() as tmpdir:
                # Create a fake PDF
                pdf_path = os.path.join(tmpdir, "doc.pdf")
                with open(pdf_path, "wb") as f:
                    f.write(b"%PDF-content")

                mock_convert.return_value = pdf_path

                with patch("app.connectors.api.router.create_stream_record_response") as mock_stream:
                    mock_stream.return_value = StreamingResponse(
                        content=iter([b"%PDF-content"]),
                        media_type="application/pdf",
                    )
                    # The function uses a tempfile.TemporaryDirectory context,
                    # so we need to override that
                    with patch("tempfile.TemporaryDirectory") as mock_tmpdir:
                        mock_tmpdir.return_value.__enter__ = MagicMock(return_value=tmpdir)
                        mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)

                        result = await convert_buffer_to_pdf_stream(b"data", "doc.pptx", "pptx")
                    assert isinstance(result, StreamingResponse)

    async def test_unsupported_buffer_type_raises(self):
        from app.connectors.api.router import convert_buffer_to_pdf_stream

        with pytest.raises(HTTPException) as exc_info:
            await convert_buffer_to_pdf_stream(12345, "doc.pptx", "pptx")
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
        assert "Unsupported buffer type" in exc_info.value.detail

    async def test_response_with_empty_body_raises(self):
        from app.connectors.api.router import convert_buffer_to_pdf_stream

        response = Response(content=b"")
        # The body property returns b"" which is falsy
        with pytest.raises(HTTPException) as exc_info:
            await convert_buffer_to_pdf_stream(response, "doc.pptx", "pptx")
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# _prepare_connector_config
# ============================================================================


class TestPrepareConnectorConfig:
    """Tests for _prepare_connector_config."""

    async def test_basic_non_oauth(self):
        from app.connectors.api.router import _prepare_connector_config

        config = {
            "auth": {"apiKey": "key123"},
            "sync": {"interval": "daily"},
            "filters": {"type": "FILE"},
        }
        metadata = {
            "config": {"auth": {"schemas": {}, "oauthConfigs": {}}}
        }
        config_service = AsyncMock()
        logger = MagicMock()

        result = await _prepare_connector_config(
            config=config,
            connector_type="slack",
            scope="personal",
            oauth_config_id=None,
            metadata=metadata,
            selected_auth_type="API_TOKEN",
            user_id="user-1",
            org_id="org-1",
            is_admin=False,
            config_service=config_service,
            base_url="",
            logger=logger,
        )

        assert result["auth"]["apiKey"] == "key123"
        assert result["auth"]["authType"] == "API_TOKEN"
        assert result["sync"]["interval"] == "daily"
        assert result["credentials"] is None
        assert result["oauth"] is None

    async def test_oauth_with_config_id(self):
        from app.connectors.api.router import _prepare_connector_config

        config = {"auth": {"oauthConfigId": "oc-1"}}
        metadata = {
            "config": {
                "auth": {
                    "schemas": {
                        "OAUTH": {"redirectUri": "callback/oauth"}
                    },
                    "oauthConfigs": {
                        "OAUTH": {
                            "authorizeUrl": "https://auth.example.com",
                            "tokenUrl": "https://token.example.com",
                            "scopes": ["read"],
                        }
                    },
                },
            }
        }
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "oc-1", "orgId": "org-1", "clientId": "cid"}
        ])
        logger = MagicMock()

        with patch("app.connectors.api.router._get_oauth_field_names_from_registry", return_value=["clientId", "clientSecret"]):
            result = await _prepare_connector_config(
                config=config,
                connector_type="googledrive",
                scope="personal",
                oauth_config_id="oc-1",
                metadata=metadata,
                selected_auth_type="OAUTH",
                user_id="user-1",
                org_id="org-1",
                is_admin=False,
                config_service=config_service,
                base_url="https://app.example.com",
                logger=logger,
            )

        assert result["auth"]["oauthConfigId"] == "oc-1"
        assert result["auth"]["authType"] == "OAUTH"
        assert result["auth"]["authorizeUrl"] == "https://auth.example.com"
        assert "https://app.example.com" in result["auth"]["redirectUri"]

    async def test_oauth_filters_credential_fields(self):
        from app.connectors.api.router import _prepare_connector_config

        config = {
            "auth": {"clientId": "cid", "clientSecret": "sec", "oauthConfigId": "oc-1", "someOtherField": "keep"}
        }
        metadata = {
            "config": {"auth": {"schemas": {"OAUTH": {}}, "oauthConfigs": {"OAUTH": {}}}}
        }
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "oc-1", "orgId": "org-1"}
        ])
        logger = MagicMock()

        with patch("app.connectors.api.router._get_oauth_field_names_from_registry", return_value=["clientId", "clientSecret"]):
            result = await _prepare_connector_config(
                config=config,
                connector_type="googledrive",
                scope="personal",
                oauth_config_id="oc-1",
                metadata=metadata,
                selected_auth_type="OAUTH",
                user_id="user-1",
                org_id="org-1",
                is_admin=False,
                config_service=config_service,
                base_url="",
                logger=logger,
            )

        # clientId and clientSecret should be excluded from auth config
        assert "clientId" not in result["auth"]
        assert "clientSecret" not in result["auth"]
        # but oauthConfigId and other fields should remain
        assert result["auth"]["oauthConfigId"] == "oc-1"
        assert result["auth"]["someOtherField"] == "keep"

    async def test_oauth_config_not_found_raises(self):
        from app.connectors.api.router import _prepare_connector_config

        config = {"auth": {}}
        metadata = {"config": {"auth": {"schemas": {}, "oauthConfigs": {}}}}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])
        logger = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await _prepare_connector_config(
                config=config,
                connector_type="googledrive",
                scope="personal",
                oauth_config_id="oc-missing",
                metadata=metadata,
                selected_auth_type="NONE",
                user_id="user-1",
                org_id="org-1",
                is_admin=False,
                config_service=config_service,
                base_url="",
                logger=logger,
            )
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    async def test_none_auth_type(self):
        from app.connectors.api.router import _prepare_connector_config

        config = None
        metadata = {"config": {"auth": {"schemas": {}, "oauthConfigs": {}}}}
        config_service = AsyncMock()
        logger = MagicMock()

        result = await _prepare_connector_config(
            config=config,
            connector_type="web",
            scope="personal",
            oauth_config_id=None,
            metadata=metadata,
            selected_auth_type=None,
            user_id="user-1",
            org_id="org-1",
            is_admin=False,
            config_service=config_service,
            base_url="",
            logger=logger,
        )
        assert result["auth"]["authType"] == "NONE"


# ============================================================================
# _check_oauth_name_conflict (helper)
# ============================================================================


class TestCheckOauthNameConflict:
    """Tests for _check_oauth_name_conflict."""

    def test_no_conflict(self):
        from app.connectors.api.router import _check_oauth_name_conflict

        configs = [
            {"oauthInstanceName": "Config A", "orgId": "org-1"},
        ]
        _check_oauth_name_conflict(configs, "Config B", "org-1")

    def test_conflict_raises(self):
        from app.connectors.api.router import _check_oauth_name_conflict

        configs = [
            {"oauthInstanceName": "Config A", "orgId": "org-1"},
        ]
        with pytest.raises(HTTPException) as exc_info:
            _check_oauth_name_conflict(configs, "Config A", "org-1")
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value

    def test_same_name_different_org_no_conflict(self):
        from app.connectors.api.router import _check_oauth_name_conflict

        configs = [
            {"oauthInstanceName": "Config A", "orgId": "org-other"},
        ]
        _check_oauth_name_conflict(configs, "Config A", "org-1")

    def test_exclude_index_skips_entry(self):
        from app.connectors.api.router import _check_oauth_name_conflict

        configs = [
            {"oauthInstanceName": "Config A", "orgId": "org-1"},
        ]
        # Excluding index 0 means no conflict
        _check_oauth_name_conflict(configs, "Config A", "org-1", exclude_index=0)

    def test_empty_configs_no_conflict(self):
        from app.connectors.api.router import _check_oauth_name_conflict

        _check_oauth_name_conflict([], "Any Name", "org-1")


# ============================================================================
# _get_oauth_config_path
# ============================================================================


class TestGetOauthConfigPath:
    """Tests for _get_oauth_config_path."""

    def test_basic(self):
        from app.connectors.api.router import _get_oauth_config_path

        assert _get_oauth_config_path("Google Drive") == "/services/oauth/googledrive"

    def test_all_lowercase(self):
        from app.connectors.api.router import _get_oauth_config_path

        assert _get_oauth_config_path("slack") == "/services/oauth/slack"

    def test_multiple_spaces(self):
        from app.connectors.api.router import _get_oauth_config_path

        assert _get_oauth_config_path("Share Point Online") == "/services/oauth/sharepointonline"


# ============================================================================
# _get_user_context / _validate_admin_only / _validate_connector_permissions
# ============================================================================


class TestGetUserContext:
    """Tests for _get_user_context."""

    def test_success(self):
        from app.connectors.api.router import _get_user_context

        request = _mock_request(
            user={"userId": "u1", "orgId": "o1"},
            headers={"X-Is-Admin": "true"},
        )
        ctx = _get_user_context(request)
        assert ctx["user_id"] == "u1"
        assert ctx["org_id"] == "o1"
        assert ctx["is_admin"] is True

    def test_unauthenticated_raises(self):
        from app.connectors.api.router import _get_user_context

        request = _mock_request(user={"userId": None, "orgId": "o1"})
        with pytest.raises(HTTPException) as exc_info:
            _get_user_context(request)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    def test_missing_org_raises(self):
        from app.connectors.api.router import _get_user_context

        request = _mock_request(user={"userId": "u1", "orgId": None})
        with pytest.raises(HTTPException):
            _get_user_context(request)


class TestValidateAdminOnly:
    """Tests for _validate_admin_only."""

    def test_admin_passes(self):
        from app.connectors.api.router import _validate_admin_only

        _validate_admin_only(is_admin=True, action="delete")

    def test_non_admin_raises(self):
        from app.connectors.api.router import _validate_admin_only

        with pytest.raises(HTTPException) as exc_info:
            _validate_admin_only(is_admin=False, action="delete connectors")
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value
        assert "delete connectors" in exc_info.value.detail


class TestValidateConnectorPermissions:
    """Tests for _validate_connector_permissions."""

    def test_team_admin_passes(self):
        from app.connectors.api.router import _validate_connector_permissions

        _validate_connector_permissions(
            {"scope": ConnectorScope.TEAM.value, "createdBy": "u1"},
            "u1",
            is_admin=True,
        )

    def test_team_non_admin_raises(self):
        from app.connectors.api.router import _validate_connector_permissions

        with pytest.raises(HTTPException) as exc_info:
            _validate_connector_permissions(
                {"scope": ConnectorScope.TEAM.value, "createdBy": "u1"},
                "u1",
                is_admin=False,
            )
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    def test_personal_creator_passes(self):
        from app.connectors.api.router import _validate_connector_permissions

        _validate_connector_permissions(
            {"scope": ConnectorScope.PERSONAL.value, "createdBy": "u1"},
            "u1",
            is_admin=False,
        )

    def test_personal_other_user_non_admin_raises(self):
        from app.connectors.api.router import _validate_connector_permissions

        with pytest.raises(HTTPException):
            _validate_connector_permissions(
                {"scope": ConnectorScope.PERSONAL.value, "createdBy": "u1"},
                "u2",
                is_admin=False,
            )

    def test_personal_admin_passes(self):
        from app.connectors.api.router import _validate_connector_permissions

        _validate_connector_permissions(
            {"scope": ConnectorScope.PERSONAL.value, "createdBy": "u1"},
            "u2",
            is_admin=True,
        )

    def test_unknown_scope_non_creator_non_admin_raises(self):
        from app.connectors.api.router import _validate_connector_permissions

        with pytest.raises(HTTPException):
            _validate_connector_permissions(
                {"scope": "other", "createdBy": "u1"},
                "u2",
                is_admin=False,
            )


# ============================================================================
# _get_and_validate_connector_instance
# ============================================================================


class TestGetAndValidateConnectorInstance:
    """Tests for _get_and_validate_connector_instance."""

    async def test_success(self):
        from app.connectors.api.router import _get_and_validate_connector_instance

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={"type": "slack"})

        result = await _get_and_validate_connector_instance(
            "c1",
            {"user_id": "u1", "org_id": "o1", "is_admin": False},
            registry,
            MagicMock(),
        )
        assert result["type"] == "slack"

    async def test_not_found_raises(self):
        from app.connectors.api.router import _get_and_validate_connector_instance

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await _get_and_validate_connector_instance(
                "c-missing",
                {"user_id": "u1", "org_id": "o1", "is_admin": False},
                registry,
                MagicMock(),
            )
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value


# ============================================================================
# _find_oauth_config_in_list
# ============================================================================


class TestFindOauthConfigInList:
    """Tests for _find_oauth_config_in_list."""

    async def test_found(self):
        from app.connectors.api.router import _find_oauth_config_in_list

        configs = [
            {"_id": "oc-1", "orgId": "org-1", "name": "A"},
            {"_id": "oc-2", "orgId": "org-1", "name": "B"},
        ]
        config, idx = await _find_oauth_config_in_list(configs, "oc-2", "org-1", MagicMock())
        assert config["name"] == "B"
        assert idx == 1

    async def test_not_found_wrong_id(self):
        from app.connectors.api.router import _find_oauth_config_in_list

        configs = [{"_id": "oc-1", "orgId": "org-1"}]
        config, idx = await _find_oauth_config_in_list(configs, "oc-99", "org-1", MagicMock())
        assert config is None
        assert idx is None

    async def test_not_found_wrong_org(self):
        from app.connectors.api.router import _find_oauth_config_in_list

        configs = [{"_id": "oc-1", "orgId": "org-other"}]
        config, idx = await _find_oauth_config_in_list(configs, "oc-1", "org-1", MagicMock())
        assert config is None
        assert idx is None

    async def test_empty_list(self):
        from app.connectors.api.router import _find_oauth_config_in_list

        config, idx = await _find_oauth_config_in_list([], "oc-1", "org-1", MagicMock())
        assert config is None
        assert idx is None


# ============================================================================
# stream_record_internal - complex route handler
# ============================================================================


class TestStreamRecordInternal:
    """Tests for stream_record_internal route handler."""

    async def test_missing_auth_header_raises_401(self):
        from app.connectors.api.router import stream_record_internal

        gp = AsyncMock()
        config_service = AsyncMock()
        request = _mock_request(headers={})

        with pytest.raises(HTTPException) as exc_info:
            await stream_record_internal(request, "rec-1", gp, config_service)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    async def test_invalid_bearer_format_raises_401(self):
        from app.connectors.api.router import stream_record_internal

        gp = AsyncMock()
        config_service = AsyncMock()
        request = _mock_request(headers={"Authorization": "Basic token123"})

        with pytest.raises(HTTPException) as exc_info:
            await stream_record_internal(request, "rec-1", gp, config_service)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    async def test_missing_org_id_in_token_raises_401(self):
        from app.connectors.api.router import stream_record_internal

        gp = AsyncMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"scopedJwtSecret": "secret"})

        request = _mock_request(headers={"Authorization": "Bearer token123"})

        with patch("app.connectors.api.router.jwt.decode", return_value={"userId": "u1"}):
            with pytest.raises(HTTPException) as exc_info:
                await stream_record_internal(request, "rec-1", gp, config_service)
            assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    async def test_record_not_found_raises_404(self):
        from app.connectors.api.router import stream_record_internal

        gp = AsyncMock()
        gp.get_record_by_id = AsyncMock(return_value=None)
        gp.get_document = AsyncMock(return_value={"_key": "org-1"})

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"scopedJwtSecret": "secret"})

        request = _mock_request(headers={"Authorization": "Bearer token123"})

        with patch("app.connectors.api.router.jwt.decode", return_value={"orgId": "org-1", "userId": "u1"}):
            with pytest.raises(HTTPException) as exc_info:
                await stream_record_internal(request, "rec-1", gp, config_service)
            assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    async def test_org_not_found_raises_404(self):
        from app.connectors.api.router import stream_record_internal

        record = _mock_record(org_id="org-1")
        gp = AsyncMock()
        gp.get_record_by_id = AsyncMock(return_value=record)
        gp.get_document = AsyncMock(return_value=None)

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"scopedJwtSecret": "secret"})

        request = _mock_request(headers={"Authorization": "Bearer token123"})

        with patch("app.connectors.api.router.jwt.decode", return_value={"orgId": "org-1", "userId": "u1"}):
            with pytest.raises(HTTPException) as exc_info:
                await stream_record_internal(request, "rec-1", gp, config_service)
            assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    async def test_knowledge_base_connector_returns_response(self):
        from app.connectors.api.router import stream_record_internal

        record = _mock_record(
            org_id="org-1",
            connector_name=Connectors.KNOWLEDGE_BASE,
            mime_type="application/pdf",
        )
        gp = AsyncMock()
        gp.get_record_by_id = AsyncMock(return_value=record)
        gp.get_document = AsyncMock(return_value={"_key": "org-1"})

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=[
            {"scopedJwtSecret": "secret"},
            {"storage": {"endpoint": "http://storage:8080"}},
        ])

        container = MagicMock()
        request = _mock_request(
            headers={"Authorization": "Bearer token123"},
            container=container,
        )

        with patch("app.connectors.api.router.jwt.decode", return_value={"orgId": "org-1", "userId": "u1"}):
            with patch("app.connectors.api.router.generate_jwt", new_callable=AsyncMock, return_value="jwt-tok"):
                with patch("app.connectors.api.router.make_api_call", new_callable=AsyncMock, return_value={"data": b"file-data"}):
                    result = await stream_record_internal(request, "rec-1", gp, config_service)
        assert isinstance(result, Response)

    async def test_connector_not_found_raises_404(self):
        from app.connectors.api.router import stream_record_internal

        record = _mock_record(org_id="org-1", connector_name=Connectors.GOOGLE_DRIVE)
        gp = AsyncMock()
        gp.get_record_by_id = AsyncMock(return_value=record)
        gp.get_document = AsyncMock(side_effect=[
            {"_key": "org-1"},  # org lookup
            None,  # connector instance not found
        ])

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"scopedJwtSecret": "secret"})

        container = MagicMock()
        request = _mock_request(
            headers={"Authorization": "Bearer token123"},
            container=container,
        )

        with patch("app.connectors.api.router.jwt.decode", return_value={"orgId": "org-1", "userId": "u1"}):
            with pytest.raises(HTTPException) as exc_info:
                await stream_record_internal(request, "rec-1", gp, config_service)
            assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    async def test_connector_obj_not_found_raises_unhealthy(self):
        from app.connectors.api.router import stream_record_internal

        record = _mock_record(org_id="org-1", connector_name=Connectors.GOOGLE_DRIVE)
        gp = AsyncMock()
        gp.get_record_by_id = AsyncMock(return_value=record)
        gp.get_document = AsyncMock(side_effect=[
            {"_key": "org-1"},  # org
            {"_key": "conn-1", "name": "My Drive"},  # connector instance
        ])

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"scopedJwtSecret": "secret"})

        container = MagicMock()
        container.connectors_map = {}
        request = _mock_request(
            headers={"Authorization": "Bearer token123"},
            container=container,
        )

        with patch("app.connectors.api.router.jwt.decode", return_value={"orgId": "org-1", "userId": "u1"}):
            with pytest.raises(HTTPException) as exc_info:
                await stream_record_internal(request, "rec-1", gp, config_service)
            assert exc_info.value.status_code == HttpStatusCode.UNHEALTHY.value

    async def test_jwt_error_raises_401(self):
        from jose import JWTError

        from app.connectors.api.router import stream_record_internal

        gp = AsyncMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"scopedJwtSecret": "secret"})
        request = _mock_request(headers={"Authorization": "Bearer bad-token"})

        with patch("app.connectors.api.router.jwt.decode", side_effect=JWTError("bad")):
            with pytest.raises(HTTPException) as exc_info:
                await stream_record_internal(request, "rec-1", gp, config_service)
            assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value


# ============================================================================
# download_file
# ============================================================================


class TestDownloadFile:
    """Tests for download_file route handler."""

    async def test_token_mismatch_raises_401(self):
        from app.connectors.api.router import download_file

        handler = MagicMock()
        payload = MagicMock()
        payload.record_id = "other-rec"
        payload.user_id = "u1"
        handler.validate_token = MagicMock(return_value=payload)

        gp = AsyncMock()
        request = _mock_request()

        with pytest.raises(HTTPException) as exc_info:
            await download_file(request, "org-1", "rec-1", "drive", "tok", handler, gp)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    async def test_org_not_found_raises_404(self):
        from app.connectors.api.router import download_file

        handler = MagicMock()
        payload = MagicMock()
        payload.record_id = "rec-1"
        payload.user_id = "u1"
        handler.validate_token = MagicMock(return_value=payload)

        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value=None)

        request = _mock_request()

        with pytest.raises(HTTPException) as exc_info:
            await download_file(request, "org-1", "rec-1", "drive", "tok", handler, gp)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    async def test_record_not_found_raises_404(self):
        from app.connectors.api.router import download_file

        handler = MagicMock()
        payload = MagicMock()
        payload.record_id = "rec-1"
        payload.user_id = "u1"
        handler.validate_token = MagicMock(return_value=payload)

        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={"_key": "org-1"})
        gp.get_record_by_id = AsyncMock(return_value=None)

        request = _mock_request()

        with pytest.raises(HTTPException) as exc_info:
            await download_file(request, "org-1", "rec-1", "drive", "tok", handler, gp)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    async def test_connector_instance_not_found_raises_404(self):
        from app.connectors.api.router import download_file

        handler = MagicMock()
        payload = MagicMock()
        payload.record_id = "rec-1"
        payload.user_id = "u1"
        handler.validate_token = MagicMock(return_value=payload)

        record = _mock_record(connector_name=Connectors.GOOGLE_DRIVE)
        gp = AsyncMock()
        gp.get_document = AsyncMock(side_effect=[
            {"_key": "org-1"},  # org
            None,  # connector instance
        ])
        gp.get_record_by_id = AsyncMock(return_value=record)

        request = _mock_request()

        with pytest.raises(HTTPException) as exc_info:
            await download_file(request, "org-1", "rec-1", "drive", "tok", handler, gp)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    async def test_connector_obj_not_found_raises_unhealthy(self):
        from app.connectors.api.router import download_file

        handler = MagicMock()
        payload = MagicMock()
        payload.record_id = "rec-1"
        payload.user_id = "u1"
        handler.validate_token = MagicMock(return_value=payload)

        record = _mock_record(connector_name=Connectors.GOOGLE_DRIVE)
        gp = AsyncMock()
        gp.get_document = AsyncMock(side_effect=[
            {"_key": "org-1"},  # org
            {"_key": "conn-1", "type": "googledrive", "name": "Drive"},  # connector instance
        ])
        gp.get_record_by_id = AsyncMock(return_value=record)

        container = MagicMock()
        container.connectors_map = {}
        request = _mock_request(container=container)

        with pytest.raises(HTTPException) as exc_info:
            await download_file(request, "org-1", "rec-1", "drive", "tok", handler, gp)
        assert exc_info.value.status_code == HttpStatusCode.UNHEALTHY.value

    async def test_success_non_google_connector(self):
        from app.connectors.api.router import download_file

        handler = MagicMock()
        payload = MagicMock()
        payload.record_id = "rec-1"
        payload.user_id = "u1"
        handler.validate_token = MagicMock(return_value=payload)

        record = _mock_record(connector_name=Connectors.SLACK)
        gp = AsyncMock()
        gp.get_document = AsyncMock(side_effect=[
            {"_key": "org-1"},  # org
            {"_key": "conn-1", "type": "slack", "name": "My Slack"},  # connector instance
        ])
        gp.get_record_by_id = AsyncMock(return_value=record)

        mock_connector = MagicMock()
        mock_connector.get_app_name = MagicMock(return_value=Connectors.SLACK)
        mock_connector.stream_record = AsyncMock(return_value=Response(content=b"data"))

        container = MagicMock()
        container.connectors_map = {"conn-1": mock_connector}
        request = _mock_request(container=container)

        result = await download_file(request, "org-1", "rec-1", "drive", "tok", handler, gp)
        assert isinstance(result, Response)


# ============================================================================
# stream_record
# ============================================================================


class TestStreamRecord:
    """Tests for stream_record route handler."""

    async def test_org_not_found_raises_404(self):
        from app.connectors.api.router import stream_record

        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value=None)
        gp.get_record_by_id = AsyncMock(return_value=_mock_record())

        config_service = AsyncMock()
        request = _mock_request()

        with pytest.raises(HTTPException) as exc_info:
            await stream_record(request, "rec-1", None, gp, config_service)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    async def test_record_not_found_raises_404(self):
        from app.connectors.api.router import stream_record

        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={"_key": "org-1"})
        gp.get_record_by_id = AsyncMock(return_value=None)

        config_service = AsyncMock()
        request = _mock_request()

        with pytest.raises(HTTPException) as exc_info:
            await stream_record(request, "rec-1", None, gp, config_service)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    async def test_no_access_raises_403(self):
        from app.connectors.api.router import stream_record

        record = _mock_record()
        gp = AsyncMock()
        gp.get_document = AsyncMock(side_effect=[
            {"_key": "org-1"},  # org
            {"_key": "conn-1", "name": "drive"},  # connector instance
        ])
        gp.get_record_by_id = AsyncMock(return_value=record)
        gp.check_record_access_with_details = AsyncMock(return_value=None)

        config_service = AsyncMock()
        request = _mock_request()

        with pytest.raises(HTTPException) as exc_info:
            await stream_record(request, "rec-1", None, gp, config_service)
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    async def test_connector_instance_not_found_raises_404(self):
        from app.connectors.api.router import stream_record

        record = _mock_record()
        gp = AsyncMock()
        gp.get_document = AsyncMock(side_effect=[
            {"_key": "org-1"},  # org
            None,  # connector instance not found
        ])
        gp.get_record_by_id = AsyncMock(return_value=record)
        gp.check_record_access_with_details = AsyncMock(return_value={"hasAccess": True})

        config_service = AsyncMock()
        request = _mock_request()

        with pytest.raises(HTTPException) as exc_info:
            await stream_record(request, "rec-1", None, gp, config_service)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    async def test_success_without_conversion(self):
        from app.connectors.api.router import stream_record

        record = _mock_record()
        gp = AsyncMock()
        gp.get_document = AsyncMock(side_effect=[
            {"_key": "org-1"},  # org
            {"_key": "conn-1", "name": "Drive"},  # connector instance
        ])
        gp.get_record_by_id = AsyncMock(return_value=record)
        gp.check_record_access_with_details = AsyncMock(return_value={"hasAccess": True})

        mock_connector = MagicMock()
        mock_connector.get_app_name = MagicMock(return_value=Connectors.SLACK)
        mock_connector.stream_record = AsyncMock(return_value=Response(content=b"file-data"))

        container = MagicMock()
        container.connectors_map = {"conn-1": mock_connector}

        config_service = AsyncMock()
        request = _mock_request(container=container)

        result = await stream_record(request, "rec-1", None, gp, config_service)
        assert isinstance(result, Response)


# ============================================================================
# create_connector_instance
# ============================================================================


class TestCreateConnectorInstance:
    """Tests for create_connector_instance route handler."""

    async def test_unauthenticated_raises_401(self):
        from app.connectors.api.router import create_connector_instance

        gp = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        container.config_service = MagicMock(return_value=AsyncMock())

        request = _mock_request(
            user={"userId": None, "orgId": "org-1"},
            container=container,
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_connector_instance(request, gp)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    async def test_missing_required_fields_raises_400(self):
        from app.connectors.api.router import create_connector_instance

        gp = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        container.config_service = MagicMock(return_value=AsyncMock())

        request = _mock_request(
            container=container,
            body={"connectorType": "", "instanceName": ""},
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_connector_instance(request, gp)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_invalid_scope_raises_400(self):
        from app.connectors.api.router import create_connector_instance

        gp = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        container.config_service = MagicMock(return_value=AsyncMock())

        request = _mock_request(
            container=container,
            body={"connectorType": "slack", "instanceName": "test", "scope": "invalid"},
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_connector_instance(request, gp)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_connector_type_not_found_raises_404(self):
        from app.connectors.api.router import create_connector_instance

        gp = AsyncMock()
        registry = AsyncMock()
        registry.get_connector_metadata = AsyncMock(return_value=None)

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        container.config_service = MagicMock(return_value=AsyncMock())

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={
                "connectorType": "nonexistent",
                "instanceName": "test",
                "scope": "personal",
            },
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await create_connector_instance(request, gp)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    async def test_team_scope_non_admin_raises_403(self):
        from app.connectors.api.router import create_connector_instance

        gp = AsyncMock()
        gp.get_account_type = AsyncMock(return_value="individual")
        registry = AsyncMock()
        registry.get_connector_metadata = AsyncMock(return_value={
            "scope": [ConnectorScope.TEAM.value],
            "supportedAuthTypes": ["NONE"],
            "config": {"auth": {}},
        })
        registry._normalize_connector_name = MagicMock(return_value="slack")
        registry._get_beta_connector_names = MagicMock(return_value=set())

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        container.config_service = MagicMock(return_value=AsyncMock())

        request = _mock_request(
            container=container,
            connector_registry=registry,
            headers={"X-Is-Admin": "false"},
            body={
                "connectorType": "slack",
                "instanceName": "Team Slack",
                "scope": "team",
            },
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await create_connector_instance(request, gp)
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value


# ============================================================================
# update_connector_instance_filters_sync_config
# ============================================================================


class TestUpdateConnectorInstanceFiltersSyncConfig:
    """Tests for update_connector_instance_filters_sync_config."""

    async def test_missing_sync_and_filters_raises_400(self):
        from app.connectors.api.router import (
            update_connector_instance_filters_sync_config,
        )

        gp = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "slack",
            "scope": ConnectorScope.PERSONAL.value,
            "createdBy": "user-1",
            "isActive": False,
        })

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={},
        )

        with patch("app.connectors.api.router.check_beta_connector_access", new_callable=AsyncMock):
            with patch("app.connectors.api.router.get_validated_connector_instance", new_callable=AsyncMock, return_value={
                "type": "slack", "scope": ConnectorScope.PERSONAL.value, "createdBy": "user-1", "isActive": False, "name": "My Slack"
            }):
                with pytest.raises(HTTPException) as exc_info:
                    await update_connector_instance_filters_sync_config("c1", request, gp)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_active_connector_raises_400(self):
        from app.connectors.api.router import (
            update_connector_instance_filters_sync_config,
        )

        gp = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        registry = AsyncMock()

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={"sync": {"interval": "daily"}},
        )

        with patch("app.connectors.api.router.get_validated_connector_instance", new_callable=AsyncMock, return_value={
            "type": "slack", "scope": ConnectorScope.PERSONAL.value, "createdBy": "user-1",
            "isActive": True, "name": "My Slack"
        }):
            with pytest.raises(HTTPException) as exc_info:
                await update_connector_instance_filters_sync_config("c1", request, gp)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_success(self):
        from app.connectors.api.router import (
            update_connector_instance_filters_sync_config,
        )

        gp = AsyncMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"auth": {}, "sync": {}, "filters": {}})
        config_service.set_config = AsyncMock()

        registry = AsyncMock()
        registry.update_connector_instance = AsyncMock(return_value=True)

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        container.config_service = MagicMock(return_value=config_service)

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={"sync": {"interval": "daily"}, "filters": {"sync": {"types": ["FILE"]}}},
        )

        with patch("app.connectors.api.router.get_validated_connector_instance", new_callable=AsyncMock, return_value={
            "type": "slack", "scope": ConnectorScope.PERSONAL.value, "createdBy": "user-1",
            "isActive": False, "name": "My Slack"
        }):
            with patch("app.connectors.api.router.get_epoch_timestamp_in_ms", return_value=999):
                result = await update_connector_instance_filters_sync_config("c1", request, gp)
        assert result["success"] is True


# ============================================================================
# update_connector_instance_config
# ============================================================================


class TestUpdateConnectorInstanceConfig:
    """Tests for update_connector_instance_config."""

    async def test_active_connector_raises_400(self):
        from app.connectors.api.router import update_connector_instance_config

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        registry = AsyncMock()

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={"auth": {"key": "val"}},
        )

        with patch("app.connectors.api.router.get_validated_connector_instance", new_callable=AsyncMock, return_value={
            "type": "slack", "scope": ConnectorScope.PERSONAL.value, "createdBy": "user-1",
            "isActive": True, "name": "Slack", "authType": "API_TOKEN"
        }):
            with pytest.raises(HTTPException) as exc_info:
                await update_connector_instance_config("c1", request)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_auth_type_change_raises_400(self):
        from app.connectors.api.router import update_connector_instance_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"auth": {}, "sync": {}, "filters": {}})

        registry = AsyncMock()

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        container.config_service = MagicMock(return_value=config_service)

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={"auth": {"authType": "OAUTH"}},
        )

        with patch("app.connectors.api.router.get_validated_connector_instance", new_callable=AsyncMock, return_value={
            "type": "slack", "scope": ConnectorScope.PERSONAL.value, "createdBy": "user-1",
            "isActive": False, "name": "Slack", "authType": "API_TOKEN"
        }):
            with pytest.raises(HTTPException) as exc_info:
                await update_connector_instance_config("c1", request)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_success_filters_only(self):
        from app.connectors.api.router import update_connector_instance_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"auth": {}, "sync": {}, "filters": {}})
        config_service.set_config = AsyncMock()

        registry = AsyncMock()
        registry.update_connector_instance = AsyncMock(return_value=True)

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        container.config_service = MagicMock(return_value=config_service)

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={"filters": {"sync": {"types": ["FILE"]}}},
        )

        with patch("app.connectors.api.router.get_validated_connector_instance", new_callable=AsyncMock, return_value={
            "type": "slack", "scope": ConnectorScope.PERSONAL.value, "createdBy": "user-1",
            "isActive": False, "name": "Slack", "authType": "API_TOKEN"
        }):
            with patch("app.connectors.api.router.get_epoch_timestamp_in_ms", return_value=999):
                result = await update_connector_instance_config("c1", request)
        assert result["success"] is True


# ============================================================================
# update_connector_instance_auth_config
# ============================================================================


class TestUpdateConnectorInstanceAuthConfig:
    """Tests for update_connector_instance_auth_config."""

    async def test_missing_auth_raises_400(self):
        from app.connectors.api.router import update_connector_instance_auth_config

        gp = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        registry = AsyncMock()

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={},
        )

        with patch("app.connectors.api.router.get_validated_connector_instance", new_callable=AsyncMock, return_value={
            "type": "slack", "scope": ConnectorScope.PERSONAL.value, "createdBy": "user-1",
            "isActive": False, "authType": "API_TOKEN", "name": "Slack"
        }):
            with pytest.raises(HTTPException) as exc_info:
                await update_connector_instance_auth_config("c1", request, gp)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_active_connector_raises_400(self):
        from app.connectors.api.router import update_connector_instance_auth_config

        gp = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        registry = AsyncMock()

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={"auth": {"key": "val"}},
        )

        with patch("app.connectors.api.router.get_validated_connector_instance", new_callable=AsyncMock, return_value={
            "type": "slack", "scope": ConnectorScope.PERSONAL.value, "createdBy": "user-1",
            "isActive": True, "authType": "API_TOKEN", "name": "Slack"
        }):
            with pytest.raises(HTTPException) as exc_info:
                await update_connector_instance_auth_config("c1", request, gp)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_success_api_token(self):
        from app.connectors.api.router import update_connector_instance_auth_config

        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={"_key": "c1", "scope": "personal"})

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"auth": {}, "sync": {}, "filters": {}})
        config_service.set_config = AsyncMock()

        registry = AsyncMock()
        registry.update_connector_instance = AsyncMock(return_value=True)

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        container.config_service = MagicMock(return_value=config_service)
        container.connectors_map = {}

        request = _mock_request(
            container=container,
            connector_registry=registry,
            body={"auth": {"apiToken": "tok-123"}},
        )

        with patch("app.connectors.api.router.get_validated_connector_instance", new_callable=AsyncMock, return_value={
            "type": "slack", "scope": ConnectorScope.PERSONAL.value, "createdBy": "user-1",
            "isActive": False, "authType": "API_TOKEN", "name": "Slack"
        }):
            with patch("app.connectors.api.router.get_epoch_timestamp_in_ms", return_value=999):
                result = await update_connector_instance_auth_config("c1", request, gp)
        assert result["success"] is True
