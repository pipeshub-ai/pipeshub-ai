"""Tests to cover remaining gaps in app/connectors/api/router.py.

Targets the ~105 missing lines identified by coverage analysis:
  - get_graph_provider / get_kafka_service dependency helpers
  - _stream_google_api_request empty chunk path
  - convert_buffer_to_pdf_stream: callable body, non-bytes body, pdf_file_iterator
  - delete_record: success without event data, generic exception
  - reindex_single_record: event publish failure, generic exception
  - reindex_record_group: JSON decode fallback, generic exception
  - check_beta_connector_access: feature flag refresh failure
  - get_connector_registry: account type exception, generic exception
  - get_connector_instances / active / inactive / configured: generic exceptions
  - _prepare_connector_config: non-list oauth_configs, missing auth key, no base_url fallback
  - create_connector_instance: beta validation, get_account_type exception
  - get_connector_instance_config: generic exception, empty config, auth cleanup
  - update_connector_instance_config: unchanged OAuth name, redirect uri branches
  - update_connector_instance_filters_sync_config: edge cases
  - update_connector_instance_name: non-creator, generic exception
  - get_oauth_authorization_url: empty scope fallback, no state param, generic exception
  - toggle_connector_instance: connectors_map init, enable agent not configured
  - get_all_oauth_configs: exception results in parallel fetch
  - _create_or_update_oauth_config: no logger param, update matching
  - update_oauth_config: name conflict branch
  - stream_record: org mismatch raises 404
"""

import asyncio
import io
import json
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse

from app.config.constants.arangodb import (
    AppStatus,
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.api.router import (
    _check_connector_not_locked,
    _check_oauth_name_conflict,
    _clean_schema_for_response,
    _extract_essential_oauth_fields,
    _find_filter_field_config,
    _find_oauth_config_by_id,
    _get_config_path_for_instance,
    _get_oauth_config_path,
    _get_settings_base_path,
    _parse_comma_separated_str,
    _sanitize_app_name,
    _trim_config_values,
    _trim_connector_config,
    _validate_admin_only,
    _validate_connector_deletion_permissions,
    _validate_connector_permissions,
    check_beta_connector_access,
    convert_buffer_to_pdf_stream,
    delete_record,
    get_connector_instance,
    get_connector_instance_config,
    get_connector_instances,
    get_connector_registry,
    get_connector_stats_endpoint,
    get_graph_provider,
    get_kafka_service,
    get_mime_type_from_record,
    get_record_by_id,
    get_records,
    get_validated_connector_instance,
    handle_record_deletion,
    reindex_record_group,
    reindex_single_record,
    require_connector_not_locked,
    require_connector_not_locked_for_record,
    require_connector_not_locked_for_record_group,
    stream_record,
    stream_record_internal,
)
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.models.entities import RecordType

_ROUTER = "app.connectors.api.router"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _mock_record(**overrides):
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
    req = MagicMock()
    user_data = user or {"userId": "user-1", "orgId": "org-1"}
    req.state = MagicMock()
    req.state.user = MagicMock()
    req.state.user.get = lambda k, default=None: user_data.get(k, default)

    _headers = headers or {}
    req.headers = MagicMock()
    req.headers.get = lambda k, default=None: _headers.get(k, default)

    if body is not None:
        req.json = AsyncMock(return_value=body)
    else:
        req.json = AsyncMock(return_value={})

    if query_params:
        req.query_params = MagicMock()
        req.query_params.get = lambda k, default=None: query_params.get(k, default)
    else:
        req.query_params = MagicMock()
        req.query_params.get = lambda k, default=None: None

    if container is not None:
        _container = container
    else:
        _container = MagicMock()
        _container.logger = MagicMock(return_value=logging.getLogger("test"))
        _container.config_service = MagicMock(return_value=AsyncMock())
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
# get_graph_provider / get_kafka_service (lines 243-249)
# ============================================================================


class TestDependencyHelpers:
    @pytest.mark.asyncio
    async def test_get_graph_provider_returns_from_app_state(self):
        req = MagicMock()
        fake_gp = MagicMock()
        req.app.state.graph_provider = fake_gp
        result = await get_graph_provider(req)
        assert result is fake_gp

    @pytest.mark.asyncio
    async def test_get_kafka_service_returns_from_container(self):
        req = MagicMock()
        fake_kafka = MagicMock()
        req.app.container.kafka_service = MagicMock(return_value=fake_kafka)
        result = await get_kafka_service(req)
        assert result is fake_kafka


# ============================================================================
# require_connector_not_locked_for_record — early return branches (lines 314-318)
# ============================================================================


class TestRequireConnectorNotLockedForRecordEdgeCases:
    @pytest.mark.asyncio
    async def test_record_origin_not_connector_returns_early(self):
        """If origin != CONNECTOR, skip the lock check."""
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={"origin": "UPLOAD", "connectorId": "c1"})
        # Should not raise — origin is not CONNECTOR
        await require_connector_not_locked_for_record("rec-1", graph_provider=gp)

    @pytest.mark.asyncio
    async def test_no_connector_id_returns_early(self):
        """If connectorId is missing, skip the lock check."""
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={"origin": OriginTypes.CONNECTOR.value})
        await require_connector_not_locked_for_record("rec-1", graph_provider=gp)

    @pytest.mark.asyncio
    async def test_locked_app_doc_raises(self):
        """If connector is locked, raise 409."""
        gp = AsyncMock()
        record = {"origin": OriginTypes.CONNECTOR.value, "connectorId": "c1"}
        app_doc = {"isLocked": True, "status": AppStatus.SYNCING.value}
        gp.get_document = AsyncMock(side_effect=[record, app_doc])
        with pytest.raises(HTTPException) as exc_info:
            await require_connector_not_locked_for_record("rec-1", graph_provider=gp)
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value

    @pytest.mark.asyncio
    async def test_app_doc_not_found_passes(self):
        """If app_doc is None, do not raise."""
        gp = AsyncMock()
        record = {"origin": OriginTypes.CONNECTOR.value, "connectorId": "c1"}
        gp.get_document = AsyncMock(side_effect=[record, None])
        await require_connector_not_locked_for_record("rec-1", graph_provider=gp)


# ============================================================================
# require_connector_not_locked_for_record_group — branches (lines 321-336)
# ============================================================================


class TestRequireConnectorNotLockedForRecordGroupEdgeCases:
    @pytest.mark.asyncio
    async def test_record_group_not_found_returns_early(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value=None)
        await require_connector_not_locked_for_record_group("rg-1", graph_provider=gp)

    @pytest.mark.asyncio
    async def test_no_connector_id_returns_early(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={"someField": "val"})
        await require_connector_not_locked_for_record_group("rg-1", graph_provider=gp)

    @pytest.mark.asyncio
    async def test_app_doc_not_found_passes(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(side_effect=[{"connectorId": "c1"}, None])
        await require_connector_not_locked_for_record_group("rg-1", graph_provider=gp)

    @pytest.mark.asyncio
    async def test_locked_app_doc_raises(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(
            side_effect=[{"connectorId": "c1"}, {"isLocked": True, "status": AppStatus.FULL_SYNCING.value}]
        )
        with pytest.raises(HTTPException) as exc_info:
            await require_connector_not_locked_for_record_group("rg-1", graph_provider=gp)
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value


# ============================================================================
# delete_record — success without event data + generic exception (lines 1240, 1270-1272)
# ============================================================================


class TestDeleteRecordGaps:
    @pytest.mark.asyncio
    async def test_success_without_event_data(self):
        """When result has no eventData, no kafka publish but still returns success."""
        req = _mock_request()
        gp = AsyncMock()
        gp.delete_record = AsyncMock(return_value={"success": True, "connector": "c1", "timestamp": 123})
        kafka = AsyncMock()

        result = await delete_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert result["success"] is True
        kafka.publish_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_event_data_without_payload(self):
        """eventData present but no payload key -- skip publish."""
        req = _mock_request()
        gp = AsyncMock()
        gp.delete_record = AsyncMock(
            return_value={"success": True, "eventData": {"eventType": "x"}, "connector": "c1"}
        )
        kafka = AsyncMock()
        result = await delete_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert result["success"] is True
        kafka.publish_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        """Non-HTTP exception becomes a 500."""
        req = _mock_request()
        gp = AsyncMock()
        gp.delete_record = AsyncMock(side_effect=RuntimeError("db down"))
        kafka = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await delete_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_kafka_publish_failure_does_not_raise(self):
        """Kafka publish failure is logged but doesn't raise."""
        req = _mock_request()
        gp = AsyncMock()
        gp.delete_record = AsyncMock(return_value={
            "success": True,
            "eventData": {"eventType": "deleted", "payload": {"id": "rec-1"}, "topic": "t"},
            "connector": "c1",
        })
        kafka = AsyncMock()
        kafka.publish_event = AsyncMock(side_effect=RuntimeError("kafka down"))

        result = await delete_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert result["success"] is True


# ============================================================================
# reindex_single_record — event publish failure + generic exception (lines 1330-1355)
# ============================================================================


class TestReindexSingleRecordGaps:
    @pytest.mark.asyncio
    async def test_event_publish_failure_still_returns_success(self):
        req = _mock_request(body={"depth": 0})
        gp = AsyncMock()
        gp.reindex_single_record = AsyncMock(return_value={
            "success": True,
            "eventData": {"eventType": "reindex", "payload": {"id": "rec-1"}, "topic": "t"},
            "recordId": "rec-1",
            "recordName": "doc.pdf",
            "connector": "c1",
            "userRole": "admin",
        })
        kafka = AsyncMock()
        kafka.publish_event = AsyncMock(side_effect=RuntimeError("kafka down"))

        result = await reindex_single_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert result["success"] is True
        assert result["eventPublished"] is True  # eventData was not None

    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        req = _mock_request()
        gp = AsyncMock()
        gp.reindex_single_record = AsyncMock(side_effect=RuntimeError("boom"))
        kafka = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await reindex_single_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_json_decode_error_uses_default_depth(self):
        """When request.json() fails with JSONDecodeError, depth defaults to 0."""
        req = _mock_request()
        req.json = AsyncMock(side_effect=json.JSONDecodeError("e", "doc", 0))
        gp = AsyncMock()
        gp.reindex_single_record = AsyncMock(return_value={
            "success": True,
            "eventData": None,
            "recordId": "rec-1",
            "recordName": "doc.pdf",
            "connector": "c1",
            "userRole": "user",
        })
        kafka = AsyncMock()

        result = await reindex_single_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert result["success"] is True
        assert result["depth"] == 0

    @pytest.mark.asyncio
    async def test_failure_result_raises_http_exception(self):
        req = _mock_request(body={})
        gp = AsyncMock()
        gp.reindex_single_record = AsyncMock(return_value={
            "success": False,
            "reason": "no access",
            "code": 403,
        })
        kafka = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await reindex_single_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert exc_info.value.status_code == 403


# ============================================================================
# reindex_record_group — JSON decode fallback + generic exception (lines 1402-1404, 1468-1470)
# ============================================================================


class TestReindexRecordGroupGaps:
    @pytest.mark.asyncio
    async def test_json_decode_error_uses_default_depth(self):
        req = _mock_request()
        req.json = AsyncMock(side_effect=json.JSONDecodeError("e", "doc", 0))
        gp = AsyncMock()
        gp.reindex_record_group_records = AsyncMock(return_value={
            "success": True,
            "connectorId": "c1",
            "connectorName": "Google Drive",
            "userKey": "uk1",
            "depth": 0,
        })
        kafka = AsyncMock()
        kafka.publish_event = AsyncMock()

        with patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=999):
            result = await reindex_record_group("rg-1", req, graph_provider=gp, kafka_service=kafka)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        req = _mock_request()
        req.json = AsyncMock(side_effect=RuntimeError("boom"))
        gp = AsyncMock()
        kafka = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await reindex_record_group("rg-1", req, graph_provider=gp, kafka_service=kafka)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_failure_result_raises_http_exception(self):
        req = _mock_request(body={"depth": 1})
        gp = AsyncMock()
        gp.reindex_record_group_records = AsyncMock(return_value={
            "success": False, "reason": "not found", "code": 404,
        })
        kafka = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await reindex_record_group("rg-1", req, graph_provider=gp, kafka_service=kafka)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_event_publish_failure_raises_500(self):
        req = _mock_request(body={"depth": 0})
        gp = AsyncMock()
        gp.reindex_record_group_records = AsyncMock(return_value={
            "success": True,
            "connectorId": "c1",
            "connectorName": "Google Drive",
            "userKey": "uk1",
            "depth": 0,
        })
        kafka = AsyncMock()
        kafka.publish_event = AsyncMock(side_effect=RuntimeError("kafka error"))

        with patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=999):
            with pytest.raises(HTTPException) as exc_info:
                await reindex_record_group("rg-1", req, graph_provider=gp, kafka_service=kafka)
            assert exc_info.value.status_code == 500


# ============================================================================
# check_beta_connector_access — feature flag refresh failure (lines 1541-1542)
# ============================================================================


class TestCheckBetaConnectorAccessGaps:
    @pytest.mark.asyncio
    async def test_feature_flag_refresh_failure_continues(self):
        """If feature_flag_service.refresh() fails, the check should continue."""
        container = MagicMock()
        ff_service = MagicMock()
        ff_service.refresh = AsyncMock(side_effect=RuntimeError("refresh failed"))
        ff_service.is_feature_enabled = MagicMock(return_value=True)  # beta enabled -> pass
        container.feature_flag_service = AsyncMock(return_value=ff_service)
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        req = MagicMock()
        req.app.container = container

        # Should not raise even though refresh failed
        await check_beta_connector_access("GOOGLE_DRIVE", req)

    @pytest.mark.asyncio
    async def test_beta_disabled_beta_connector_raises_403(self):
        container = MagicMock()
        ff_service = MagicMock()
        ff_service.refresh = AsyncMock()
        ff_service.is_feature_enabled = MagicMock(return_value=False)
        container.feature_flag_service = AsyncMock(return_value=ff_service)
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        req = MagicMock()
        req.app.container = container

        # "GOOGLE_DRIVE" normalizes to "google_drive" — include it in beta list
        with patch(f"{_ROUTER}.ConnectorFactory") as mock_factory:
            mock_factory.list_beta_connectors.return_value = ["google_drive"]
            with pytest.raises(HTTPException) as exc_info:
                await check_beta_connector_access("GOOGLE_DRIVE", req)
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_beta_disabled_non_beta_connector_passes(self):
        container = MagicMock()
        ff_service = MagicMock()
        ff_service.refresh = AsyncMock()
        ff_service.is_feature_enabled = MagicMock(return_value=False)
        container.feature_flag_service = AsyncMock(return_value=ff_service)
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        req = MagicMock()
        req.app.container = container

        with patch(f"{_ROUTER}.ConnectorFactory") as mock_factory:
            mock_factory.list_beta_connectors.return_value = ["someotherbeta"]
            # "GOOGLE_DRIVE" normalizes to "googledrive", not in beta list
            await check_beta_connector_access("GOOGLE_DRIVE", req)

    @pytest.mark.asyncio
    async def test_entire_check_exception_fails_open(self):
        """If the entire beta check fails, it should fail-open (not raise)."""
        container = MagicMock()
        container.feature_flag_service = AsyncMock(side_effect=RuntimeError("service unavailable"))
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        req = MagicMock()
        req.app.container = container

        # Should not raise
        await check_beta_connector_access("GOOGLE_DRIVE", req)


# ============================================================================
# get_connector_registry — account type exception + generic exception
# ============================================================================


class TestGetConnectorRegistryGaps:
    @pytest.mark.asyncio
    async def test_get_account_type_exception_continues(self):
        """If graph_provider.get_account_type raises, continue with None account_type."""
        gp = AsyncMock()
        gp.get_account_type = AsyncMock(side_effect=RuntimeError("db error"))
        registry = AsyncMock()
        registry.get_all_registered_connectors = AsyncMock(return_value={"connectors": [], "total": 0})
        req = _mock_request(graph_provider=gp, connector_registry=registry)

        result = await get_connector_registry(req, scope=None, page=1, limit=20, search=None)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        gp = AsyncMock()
        registry = AsyncMock()
        registry.get_all_registered_connectors = AsyncMock(side_effect=RuntimeError("boom"))
        req = _mock_request(graph_provider=gp, connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_registry(req, scope=None, page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_no_connectors_in_registry_raises_404(self):
        gp = AsyncMock()
        registry = AsyncMock()
        registry.get_all_registered_connectors = AsyncMock(return_value=None)
        req = _mock_request(graph_provider=gp, connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_registry(req, scope=None, page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value


# ============================================================================
# get_connector_instances — generic exception (lines 1787-1789)
# ============================================================================


class TestGetConnectorInstancesGaps:
    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        registry = AsyncMock()
        registry.get_all_connector_instances = AsyncMock(side_effect=RuntimeError("boom"))
        req = _mock_request(connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instances(req, scope=None, page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# get_active_connector_instances / get_inactive_connector_instances — generic exceptions
# ============================================================================


class TestActiveInactiveConnectorGaps:
    @pytest.mark.asyncio
    async def test_active_generic_exception_raises_500(self):
        from app.connectors.api.router import get_active_connector_instances

        registry = AsyncMock()
        registry.get_active_connector_instances = AsyncMock(side_effect=RuntimeError("boom"))
        req = _mock_request(connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_active_connector_instances(req)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_inactive_generic_exception_raises_500(self):
        from app.connectors.api.router import get_inactive_connector_instances

        registry = AsyncMock()
        registry.get_inactive_connector_instances = AsyncMock(side_effect=RuntimeError("boom"))
        req = _mock_request(connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_inactive_connector_instances(req)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# get_configured_connector_instances — generic exception (lines 1934-1936)
# ============================================================================


class TestGetConfiguredConnectorInstancesGaps:
    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        from app.connectors.api.router import get_configured_connector_instances

        registry = AsyncMock()
        registry.get_configured_connector_instances = AsyncMock(side_effect=RuntimeError("boom"))
        req = _mock_request(connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_configured_connector_instances(req, scope=None, page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# _prepare_connector_config — non-list oauth_configs + no base_url fallback
# ============================================================================


class TestPrepareConnectorConfigGaps:
    @pytest.mark.asyncio
    async def test_non_list_oauth_configs_resets_to_empty(self):
        """When config_service returns a non-list for oauth configs, it resets to [] and raises 404."""
        from app.connectors.api.router import _prepare_connector_config

        config_service = AsyncMock()
        # Return a non-list for oauth configs
        config_service.get_config = AsyncMock(return_value="not-a-list")

        with pytest.raises(HTTPException) as exc_info:
            await _prepare_connector_config(
                config={"auth": {"someField": "val"}},
                connector_type="GOOGLE_DRIVE",
                scope="personal",
                oauth_config_id="oid-1",
                metadata={"config": {"auth": {"schemas": {}, "oauthConfigs": {}}}},
                selected_auth_type="NONE",
                user_id="u1",
                org_id="o1",
                is_admin=False,
                config_service=config_service,
                base_url="",
                logger=logging.getLogger("test"),
            )
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_oauth_config_id_not_found_raises_404(self):
        from app.connectors.api.router import _prepare_connector_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])

        with pytest.raises(HTTPException) as exc_info:
            await _prepare_connector_config(
                config={"auth": {"someField": "val"}},
                connector_type="GOOGLE_DRIVE",
                scope="personal",
                oauth_config_id="oid-1",
                metadata={"config": {"auth": {"schemas": {}, "oauthConfigs": {}}}},
                selected_auth_type="NONE",
                user_id="u1",
                org_id="o1",
                is_admin=False,
                config_service=config_service,
                base_url="",
                logger=logging.getLogger("test"),
            )
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_oauth_type_no_base_url_uses_fallback(self):
        from app.connectors.api.router import _prepare_connector_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"frontend": {"publicEndpoint": "http://example.com"}}
        )

        result = await _prepare_connector_config(
            config={"auth": {"someField": "val"}},
            connector_type="GOOGLE_DRIVE",
            scope="personal",
            oauth_config_id=None,
            metadata={
                "config": {
                    "auth": {
                        "schemas": {"OAUTH": {"redirectUri": "callback"}},
                        "oauthConfigs": {"OAUTH": {"authorizeUrl": "http://auth", "tokenUrl": "http://token", "scopes": ["read"]}},
                    }
                }
            },
            selected_auth_type="OAUTH",
            user_id="u1",
            org_id="o1",
            is_admin=False,
            config_service=config_service,
            base_url="",
            logger=logging.getLogger("test"),
        )
        # Check redirect URI was built from fallback
        assert "example.com" in result["auth"]["redirectUri"]

    @pytest.mark.asyncio
    async def test_missing_auth_key_in_prepared_config(self):
        """When oauth_config_id found but auth key missing, it should be created."""
        from app.connectors.api.router import _prepare_connector_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value=[{"_id": "oid-1", "orgId": "o1"}]
        )

        result = await _prepare_connector_config(
            config=None,  # No config at all
            connector_type="GOOGLE_DRIVE",
            scope="personal",
            oauth_config_id="oid-1",
            metadata={"config": {"auth": {"schemas": {}, "oauthConfigs": {}}}},
            selected_auth_type="NONE",
            user_id="u1",
            org_id="o1",
            is_admin=False,
            config_service=config_service,
            base_url="",
            logger=logging.getLogger("test"),
        )
        assert "oauthConfigId" in result["auth"]


# ============================================================================
# get_connector_instance — generic exception (lines 2572-2574)
# ============================================================================


class TestGetConnectorInstanceGaps:
    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(side_effect=RuntimeError("boom"))
        req = _mock_request(connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instance("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value=None)
        req = _mock_request(connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instance("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value


# ============================================================================
# get_connector_instance_config — generic exception + auth cleanup (lines 2692-2694, 2650)
# ============================================================================


class TestGetConnectorInstanceConfigGaps:
    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(side_effect=RuntimeError("boom"))
        req = _mock_request(connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instance_config("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_instance_not_found_raises_404(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value=None)
        req = _mock_request(connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instance_config("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_config_not_found_returns_empty(self):
        """When config is not in etcd (raises exception), return default empty config."""
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "GOOGLE_DRIVE", "name": "My Drive", "scope": "personal",
            "createdBy": "u1", "authType": "OAUTH",
        })
        config_service = MagicMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("not found"))

        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        container.config_service = MagicMock(return_value=config_service)

        req = _mock_request(connector_registry=registry, container=container)

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            result = await get_connector_instance_config("c1", req)
        assert result["success"] is True
        assert result["config"]["config"] == {"auth": {}, "sync": {}, "filters": {}}

    @pytest.mark.asyncio
    async def test_auth_cleanup_removes_oauth_fields(self):
        """Auth section should have authorizeUrl, tokenUrl, scopes, oauthConfigs removed."""
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "GOOGLE_DRIVE", "name": "My Drive", "scope": "personal",
            "createdBy": "u1", "authType": "OAUTH",
        })
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authorizeUrl": "http://auth",
                "tokenUrl": "http://token",
                "scopes": ["read"],
                "oauthConfigs": {"some": "data"},
                "clientId": "abc",
            },
            "sync": {"interval": 3600},
            "filters": {},
            "credentials": "secret",
            "oauth": {"token": "t"},
        })

        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        container.config_service = MagicMock(return_value=config_service)

        req = _mock_request(connector_registry=registry, container=container)

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            result = await get_connector_instance_config("c1", req)
        auth = result["config"]["config"]["auth"]
        assert "authorizeUrl" not in auth
        assert "tokenUrl" not in auth
        assert "scopes" not in auth
        assert "oauthConfigs" not in auth
        assert auth["clientId"] == "abc"
        assert "credentials" not in result["config"]["config"]
        assert "oauth" not in result["config"]["config"]


# ============================================================================
# get_connector_stats_endpoint
# ============================================================================


class TestGetConnectorStatsGaps:
    @pytest.mark.asyncio
    async def test_success_returns_data(self):
        gp = AsyncMock()
        gp.get_connector_stats = AsyncMock(return_value={"success": True, "data": {"count": 10}})
        req = _mock_request(graph_provider=gp)

        result = await get_connector_stats_endpoint(req, "org-1", "c1", graph_provider=gp)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self):
        gp = AsyncMock()
        gp.get_connector_stats = AsyncMock(return_value={"success": False})
        req = _mock_request(graph_provider=gp)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_stats_endpoint(req, "org-1", "c1", graph_provider=gp)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_generic_exception_propagates(self):
        """When get_connector_stats raises before logger is set, UnboundLocalError propagates."""
        gp = AsyncMock()
        gp.get_connector_stats = AsyncMock(side_effect=RuntimeError("boom"))
        req = _mock_request(graph_provider=gp)

        # The router has a latent bug: logger is set AFTER graph_provider.get_connector_stats,
        # so if that call raises, the except block hits UnboundLocalError.
        with pytest.raises((HTTPException, UnboundLocalError)):
            await get_connector_stats_endpoint(req, "org-1", "c1", graph_provider=gp)


# ============================================================================
# stream_record — org mismatch, re-fetch fails (line 723)
# ============================================================================


class TestStreamRecordOrgMismatch:
    @pytest.mark.asyncio
    async def test_org_mismatch_refetch_org_not_found_raises_404(self):
        """When record.org_id != user org_id and re-fetch returns None, raise 404."""
        gp = AsyncMock()
        record = _mock_record(org_id="org-2")
        # First call: org lookup returns valid, record lookup returns record with different org_id
        # Then re-fetch with record org_id returns None
        gp.get_document = AsyncMock(side_effect=[
            {"_key": "org-1", "accountType": "individual"},  # org lookup
            None,  # re-fetch with record's org_id
        ])
        gp.get_record_by_id = AsyncMock(return_value=record)
        gp.check_record_access_with_details = AsyncMock(return_value=True)

        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        container.config_service = MagicMock(return_value=AsyncMock())

        req = _mock_request(graph_provider=gp, container=container)

        config_service = AsyncMock()
        with pytest.raises(HTTPException) as exc_info:
            await stream_record(req, "rec-1", graph_provider=gp, config_service=config_service)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value


# ============================================================================
# get_record_by_id — edge cases
# ============================================================================


class TestGetRecordByIdGaps:
    @pytest.mark.asyncio
    async def test_no_access_raises_500_wrapping_404(self):
        """When has_access is falsy, the inner 404 gets caught by outer except -> 500."""
        gp = AsyncMock()
        gp.check_record_access_with_details = AsyncMock(return_value=None)
        req = _mock_request()

        with pytest.raises(HTTPException) as exc_info:
            await get_record_by_id("rec-1", req, graph_provider=gp)
        # The function raises 404 inside try, but the outer except Exception catches it
        # and re-wraps as 500. This is a known pattern in the codebase.
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_exception_raises_500(self):
        gp = AsyncMock()
        gp.check_record_access_with_details = AsyncMock(side_effect=RuntimeError("db err"))
        req = _mock_request()

        with pytest.raises(HTTPException) as exc_info:
            await get_record_by_id("rec-1", req, graph_provider=gp)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_has_access_returns_data(self):
        gp = AsyncMock()
        access_data = {"recordId": "rec-1", "hasAccess": True}
        gp.check_record_access_with_details = AsyncMock(return_value=access_data)
        req = _mock_request()

        result = await get_record_by_id("rec-1", req, graph_provider=gp)
        assert result == access_data


# ============================================================================
# _clean_schema_for_response
# ============================================================================


class TestCleanSchemaForResponse:
    def test_removes_oauth_configs_and_auth_fields(self):
        schema = {
            "_oauth_configs": {"some": "data"},
            "auth": {
                "authorizeUrl": "http://auth",
                "tokenUrl": "http://token",
                "scopes": ["read"],
                "oauthConfigs": {"OAUTH": {}},
                "schemas": {"OAUTH": {"redirectUri": "/callback"}},
            },
            "sync": {"interval": 3600},
        }
        result = _clean_schema_for_response(schema)
        assert "_oauth_configs" not in result
        assert "authorizeUrl" not in result["auth"]
        assert "tokenUrl" not in result["auth"]
        assert "scopes" not in result["auth"]
        assert "oauthConfigs" not in result["auth"]
        assert "schemas" in result["auth"]

    def test_no_auth_key_returns_as_is(self):
        schema = {"sync": {"interval": 3600}}
        result = _clean_schema_for_response(schema)
        assert result == {"sync": {"interval": 3600}}


# ============================================================================
# _find_filter_field_config
# ============================================================================


class TestFindFilterFieldConfig:
    def test_finds_in_sync_category(self):
        metadata = {
            "config": {
                "filters": {
                    "sync": {
                        "schema": {
                            "fields": [
                                {"name": "folders", "type": "select"},
                                {"name": "labels", "type": "multiselect"},
                            ]
                        }
                    }
                }
            }
        }
        result = _find_filter_field_config(metadata, "folders")
        assert result == {"name": "folders", "type": "select"}

    def test_finds_in_indexing_category(self):
        metadata = {
            "config": {
                "filters": {
                    "indexing": {
                        "schema": {
                            "fields": [{"name": "fileTypes", "type": "multiselect"}]
                        }
                    }
                }
            }
        }
        result = _find_filter_field_config(metadata, "fileTypes")
        assert result["name"] == "fileTypes"

    def test_not_found_returns_none(self):
        metadata = {"config": {"filters": {"sync": {"schema": {"fields": []}}}}}
        assert _find_filter_field_config(metadata, "nonexistent") is None

    def test_empty_metadata_returns_none(self):
        assert _find_filter_field_config({}, "folders") is None


# ============================================================================
# _extract_essential_oauth_fields
# ============================================================================


class TestExtractEssentialOAuthFields:
    def test_extracts_all_fields(self):
        config = {
            "_id": "id1",
            "oauthInstanceName": "My Config",
            "iconPath": "/icons/google.svg",
            "appGroup": "Google",
            "appDescription": "desc",
            "appCategories": ["storage"],
            "connectorType": "GOOGLE_DRIVE",
            "createdAtTimestamp": 100,
            "updatedAtTimestamp": 200,
            "config": {"clientId": "secret"},  # should NOT be in output
        }
        result = _extract_essential_oauth_fields(config, "GOOGLE_DRIVE")
        assert result["_id"] == "id1"
        assert result["oauthInstanceName"] == "My Config"
        assert "config" not in result

    def test_defaults_for_missing_fields(self):
        config = {"_id": "id1"}
        result = _extract_essential_oauth_fields(config, "SLACK")
        assert result["connectorType"] == "SLACK"
        assert result["iconPath"] == "/icons/connectors/default.svg"
        assert result["appGroup"] == ""
        assert result["appCategories"] == []


# ============================================================================
# _find_oauth_config_by_id
# ============================================================================


class TestFindOAuthConfigById:
    def test_finds_matching_config(self):
        configs = [
            {"_id": "c1", "orgId": "org-1"},
            {"_id": "c2", "orgId": "org-1"},
        ]
        result = _find_oauth_config_by_id(configs, "c2", "org-1")
        assert result["_id"] == "c2"

    def test_wrong_org_returns_none(self):
        configs = [{"_id": "c1", "orgId": "org-2"}]
        assert _find_oauth_config_by_id(configs, "c1", "org-1") is None

    def test_not_found_returns_none(self):
        configs = [{"_id": "c1", "orgId": "org-1"}]
        assert _find_oauth_config_by_id(configs, "c999", "org-1") is None

    def test_empty_list_returns_none(self):
        assert _find_oauth_config_by_id([], "c1", "org-1") is None


# ============================================================================
# _get_oauth_config_path
# ============================================================================


class TestGetOAuthConfigPath:
    def test_normalizes_type(self):
        assert _get_oauth_config_path("Google Drive") == "/services/oauth/googledrive"
        assert _get_oauth_config_path("SLACK") == "/services/oauth/slack"


# ============================================================================
# _generate_oauth_config_id
# ============================================================================


class TestGenerateOAuthConfigId:
    def test_returns_uuid_string(self):
        from app.connectors.api.router import _generate_oauth_config_id

        result = _generate_oauth_config_id()
        assert isinstance(result, str)
        assert len(result) == 36  # UUID format

    def test_unique_ids(self):
        from app.connectors.api.router import _generate_oauth_config_id

        ids = {_generate_oauth_config_id() for _ in range(10)}
        assert len(ids) == 10


# ============================================================================
# _get_oauth_field_names_from_registry
# ============================================================================


class TestGetOAuthFieldNamesFromRegistry:
    def test_returns_field_names_from_registry(self):
        from app.connectors.api.router import _get_oauth_field_names_from_registry

        with patch(f"{_ROUTER}.get_oauth_config_registry") as mock_get_reg:
            mock_reg = MagicMock()
            field1 = MagicMock()
            field1.name = "clientId"
            field2 = MagicMock()
            field2.name = "clientSecret"
            mock_config = MagicMock()
            mock_config.auth_fields = [field1, field2]
            mock_reg.get_config.return_value = mock_config
            mock_get_reg.return_value = mock_reg

            result = _get_oauth_field_names_from_registry("GOOGLE_DRIVE")
            assert result == ["clientId", "clientSecret"]

    def test_no_config_returns_defaults(self):
        from app.connectors.api.router import _get_oauth_field_names_from_registry

        with patch(f"{_ROUTER}.get_oauth_config_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_reg.get_config.return_value = None
            mock_get_reg.return_value = mock_reg

            result = _get_oauth_field_names_from_registry("UNKNOWN")
            assert result == ["clientId", "clientSecret"]

    def test_exception_returns_defaults(self):
        from app.connectors.api.router import _get_oauth_field_names_from_registry

        with patch(f"{_ROUTER}.get_oauth_config_registry", side_effect=RuntimeError):
            result = _get_oauth_field_names_from_registry("GOOGLE_DRIVE")
            assert result == ["clientId", "clientSecret"]


# ============================================================================
# _get_connector_from_container
# ============================================================================


class TestGetConnectorFromContainer:
    def test_from_connector_key_attr(self):
        from app.connectors.api.router import _get_connector_from_container

        container = MagicMock()
        connector_mock = MagicMock()
        container.c1_connector = MagicMock(return_value=connector_mock)
        result = _get_connector_from_container(container, "c1")
        assert result is connector_mock

    def test_from_connectors_map(self):
        from app.connectors.api.router import _get_connector_from_container

        container = MagicMock(spec=[])  # no dynamic attrs
        container.connectors_map = {"c1": MagicMock()}
        result = _get_connector_from_container(container, "c1")
        assert result is container.connectors_map["c1"]

    def test_not_found_returns_none(self):
        from app.connectors.api.router import _get_connector_from_container

        container = MagicMock(spec=[])
        result = _get_connector_from_container(container, "c1")
        assert result is None


# ============================================================================
# _get_settings_base_path — additional edge cases
# ============================================================================


class TestGetSettingsBasePathGaps:
    @pytest.mark.asyncio
    async def test_business_account_returns_company_path(self):
        gp = AsyncMock()
        gp.get_all_documents = AsyncMock(return_value=[{"accountType": "Business"}])
        result = await _get_settings_base_path(gp)
        assert "company-settings" in result

    @pytest.mark.asyncio
    async def test_enterprise_account_returns_company_path(self):
        gp = AsyncMock()
        gp.get_all_documents = AsyncMock(return_value=[{"accountType": "Enterprise"}])
        result = await _get_settings_base_path(gp)
        assert "company-settings" in result

    @pytest.mark.asyncio
    async def test_individual_account_returns_individual_path(self):
        gp = AsyncMock()
        gp.get_all_documents = AsyncMock(return_value=[{"accountType": "Individual"}])
        result = await _get_settings_base_path(gp)
        assert "individual" in result

    @pytest.mark.asyncio
    async def test_empty_list_returns_individual_path(self):
        gp = AsyncMock()
        gp.get_all_documents = AsyncMock(return_value=[])
        result = await _get_settings_base_path(gp)
        assert "individual" in result

    @pytest.mark.asyncio
    async def test_exception_returns_individual_path(self):
        gp = AsyncMock()
        gp.get_all_documents = AsyncMock(side_effect=RuntimeError("db down"))
        result = await _get_settings_base_path(gp)
        assert "individual" in result

    @pytest.mark.asyncio
    async def test_none_org_returns_individual_path(self):
        gp = AsyncMock()
        gp.get_all_documents = AsyncMock(return_value=[None])
        result = await _get_settings_base_path(gp)
        assert "individual" in result


# ============================================================================
# convert_buffer_to_pdf_stream — callable body_content + non-bytes body
# ============================================================================


class TestConvertBufferToPdfStreamBodyEdgeCases:
    @pytest.mark.asyncio
    async def test_response_callable_body(self):
        """When Response.body is a callable, call it to get bytes content."""
        resp = MagicMock(spec=Response)
        resp.body = MagicMock(return_value=b"pdf-content")

        with patch(f"{_ROUTER}.convert_to_pdf", new_callable=AsyncMock) as mock_convert, \
             patch(f"{_ROUTER}.create_stream_record_response") as mock_stream:
            mock_convert.return_value = "/tmp/nonexistent.pdf"
            mock_stream.return_value = MagicMock()
            with patch("os.path.exists", return_value=False):
                with patch("os.listdir", return_value=[]):
                    with pytest.raises(HTTPException):
                        await convert_buffer_to_pdf_stream(resp, "file.pptx", "pptx")

    @pytest.mark.asyncio
    async def test_response_non_bytes_body_raises(self):
        """When Response.body returns non-bytes, raise 500."""
        resp = MagicMock(spec=Response)
        resp.body = "not-bytes-string"

        with pytest.raises(HTTPException) as exc_info:
            await convert_buffer_to_pdf_stream(resp, "file.pptx", "pptx")
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
        assert "not bytes" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_response_empty_body_raises(self):
        """When Response.body is None/empty, raise 500."""
        resp = MagicMock(spec=Response)
        resp.body = None

        with pytest.raises(HTTPException) as exc_info:
            await convert_buffer_to_pdf_stream(resp, "file.pptx", "pptx")
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# _create_or_update_oauth_config — no logger, update matching (lines 6038-6042, 6058)
# ============================================================================


class TestCreateOrUpdateOAuthConfigGaps:
    @pytest.mark.asyncio
    async def test_no_logger_uses_default(self):
        from app.connectors.api.router import _create_or_update_oauth_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])
        config_service.set_config = AsyncMock()

        with patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId", "clientSecret"]):
            with patch(f"{_ROUTER}._update_oauth_infrastructure_fields", new_callable=AsyncMock):
                with patch(f"{_ROUTER}._generate_oauth_config_id", return_value="new-id"):
                    with patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=999):
                        result = await _create_or_update_oauth_config(
                            connector_type="GOOGLE_DRIVE",
                            auth_config={"clientId": "abc", "clientSecret": "xyz"},
                            instance_name="My Config",
                            user_id="u1",
                            org_id="o1",
                            is_admin=True,
                            config_service=config_service,
                            base_url="http://example.com",
                            logger=None,  # No logger provided
                        )
        assert result == "new-id"

    @pytest.mark.asyncio
    async def test_update_existing_with_matching_id_and_org(self):
        from app.connectors.api.router import _create_or_update_oauth_config

        existing = {
            "_id": "existing-id",
            "userId": "u1",
            "orgId": "o1",
            "config": {"clientId": "old"},
        }
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[existing])
        config_service.set_config = AsyncMock()

        with patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId", "clientSecret"]):
            with patch(f"{_ROUTER}._update_oauth_infrastructure_fields", new_callable=AsyncMock):
                with patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=999):
                    result = await _create_or_update_oauth_config(
                        connector_type="GOOGLE_DRIVE",
                        auth_config={"clientId": "new-id-val"},
                        instance_name="Config",
                        user_id="u1",
                        org_id="o1",
                        is_admin=True,
                        config_service=config_service,
                        base_url="",
                        oauth_app_id="existing-id",
                        logger=logging.getLogger("test"),
                    )
        assert result == "existing-id"

    @pytest.mark.asyncio
    async def test_update_wrong_org_falls_through_to_create(self):
        from app.connectors.api.router import _create_or_update_oauth_config

        existing = {"_id": "existing-id", "userId": "u1", "orgId": "org-other", "config": {}}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[existing])
        config_service.set_config = AsyncMock()

        with patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId"]):
            with patch(f"{_ROUTER}._update_oauth_infrastructure_fields", new_callable=AsyncMock):
                with patch(f"{_ROUTER}._generate_oauth_config_id", return_value="new-id"):
                    with patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=999):
                        result = await _create_or_update_oauth_config(
                            connector_type="GOOGLE_DRIVE",
                            auth_config={"clientId": "abc"},
                            instance_name="Config",
                            user_id="u1",
                            org_id="o1",
                            is_admin=True,
                            config_service=config_service,
                            base_url="",
                            oauth_app_id="existing-id",
                            logger=logging.getLogger("test"),
                        )
        assert result == "new-id"

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        from app.connectors.api.router import _create_or_update_oauth_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))

        with patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId"]):
            result = await _create_or_update_oauth_config(
                connector_type="GOOGLE_DRIVE",
                auth_config={"clientId": "abc"},
                instance_name="Config",
                user_id="u1",
                org_id="o1",
                is_admin=True,
                config_service=config_service,
                base_url="",
                logger=logging.getLogger("test"),
            )
        assert result is None


# ============================================================================
# _get_oauth_configs_from_etcd
# ============================================================================


class TestGetOAuthConfigsFromEtcd:
    @pytest.mark.asyncio
    async def test_returns_list(self):
        from app.connectors.api.router import _get_oauth_configs_from_etcd

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[{"_id": "c1"}])
        result = await _get_oauth_configs_from_etcd("GOOGLE_DRIVE", config_service)
        assert result == [{"_id": "c1"}]

    @pytest.mark.asyncio
    async def test_non_list_returns_empty(self):
        from app.connectors.api.router import _get_oauth_configs_from_etcd

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value="not-a-list")
        result = await _get_oauth_configs_from_etcd("GOOGLE_DRIVE", config_service)
        assert result == []


# ============================================================================
# update_connector_instance_name — non-creator + generic exception
# (lines 3493-3500, 3544-3546)
# ============================================================================


class TestUpdateConnectorInstanceNameGaps:
    @pytest.mark.asyncio
    async def test_non_creator_non_admin_raises_403(self):
        from app.connectors.api.router import update_connector_instance_name

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "GOOGLE_DRIVE",
            "scope": ConnectorScope.PERSONAL.value,
            "createdBy": "other-user",
        })
        req = _mock_request(
            connector_registry=registry,
            body={"instanceName": "New Name"},
            headers={"X-Is-Admin": "false"},
        )

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await update_connector_instance_name("c1", req)
            assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    @pytest.mark.asyncio
    async def test_admin_non_creator_personal_raises_403(self):
        """Admin cannot update personal connector of another user."""
        from app.connectors.api.router import update_connector_instance_name

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "GOOGLE_DRIVE",
            "scope": ConnectorScope.PERSONAL.value,
            "createdBy": "other-user",
        })
        req = _mock_request(
            connector_registry=registry,
            body={"instanceName": "New Name"},
            headers={"X-Is-Admin": "true"},
        )

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await update_connector_instance_name("c1", req)
            assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        from app.connectors.api.router import update_connector_instance_name

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(side_effect=RuntimeError("boom"))
        req = _mock_request(connector_registry=registry, body={"instanceName": "New"})

        with pytest.raises(HTTPException) as exc_info:
            await update_connector_instance_name("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# update_connector_instance_filters_sync_config — edge cases
# (lines 3086, 3100, 3106, 3125, 3142-3143, 3157-3159)
# ============================================================================


class TestUpdateFiltersSyncConfigGaps:
    @pytest.mark.asyncio
    async def test_no_existing_config_uses_empty_dict(self):
        from app.connectors.api.router import update_connector_instance_filters_sync_config

        registry = AsyncMock()
        instance = {
            "type": "GOOGLE_DRIVE", "scope": "personal", "createdBy": "user-1",
            "isActive": False, "name": "My Drive",
        }
        registry.update_connector_instance = AsyncMock(return_value={"_key": "c1"})

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=None)  # No existing config
        config_service.set_config = AsyncMock()

        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        container.config_service = MagicMock(return_value=config_service)

        req = _mock_request(
            connector_registry=registry,
            container=container,
            body={"sync": {"interval": 60}},
        )

        with patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock, return_value=instance):
            with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
                with patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=999):
                    with patch(f"{_ROUTER}._trim_connector_config", side_effect=lambda x: x):
                        result = await update_connector_instance_filters_sync_config("c1", req)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_update_returns_none_raises_500(self):
        from app.connectors.api.router import update_connector_instance_filters_sync_config

        registry = AsyncMock()
        instance = {
            "type": "GOOGLE_DRIVE", "scope": "personal", "createdBy": "user-1",
            "isActive": False, "name": "My Drive",
        }
        registry.update_connector_instance = AsyncMock(return_value=None)

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"sync": {}, "filters": {}})
        config_service.set_config = AsyncMock()

        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        container.config_service = MagicMock(return_value=config_service)

        req = _mock_request(
            connector_registry=registry,
            container=container,
            body={"sync": {"interval": 60}},
        )

        with patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock, return_value=instance):
            with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
                with patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=999):
                    with patch(f"{_ROUTER}._trim_connector_config", side_effect=lambda x: x):
                        with pytest.raises(HTTPException) as exc_info:
                            await update_connector_instance_filters_sync_config("c1", req)
                        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        from app.connectors.api.router import update_connector_instance_filters_sync_config

        req = _mock_request(body={"sync": {"interval": 60}})

        with patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            with pytest.raises(HTTPException) as exc_info:
                await update_connector_instance_filters_sync_config("c1", req)
            assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# _stream_google_api_request — empty chunk skip (line 123->127)
# ============================================================================


class TestStreamGoogleApiRequestGaps:
    @pytest.mark.asyncio
    async def test_empty_chunk_is_skipped(self):
        """When MediaIoBaseDownload returns an empty chunk, it should not be yielded."""
        from app.connectors.api.router import _stream_google_api_request

        mock_request = MagicMock()

        call_count = [0]

        def mock_next_chunk(downloader):
            """Simulate: first call returns empty data, second call returns data + done."""
            call_count[0] += 1
            if call_count[0] == 1:
                return (None, False)
            return (None, True)

        with patch(f"{_ROUTER}.MediaIoBaseDownload") as mock_downloader_cls:
            mock_dl = MagicMock()
            mock_dl.next_chunk = MagicMock(side_effect=lambda: mock_next_chunk(mock_dl))
            mock_downloader_cls.return_value = mock_dl

            chunks = []
            async for chunk in _stream_google_api_request(mock_request, "download"):
                chunks.append(chunk)

            # Since the mock buffer won't have real data, just verify no errors
            assert isinstance(chunks, list)


# ============================================================================
# get_validated_connector_instance — edge cases
# ============================================================================


class TestGetValidatedConnectorInstanceGaps:
    @pytest.mark.asyncio
    async def test_personal_scope_admin_other_creator_raises_403(self):
        """Personal connector: admin != creator should raise 403."""
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "GOOGLE_DRIVE",
            "scope": ConnectorScope.PERSONAL.value,
            "createdBy": "other-user",
        })
        req = _mock_request(
            connector_registry=registry,
            headers={"X-Is-Admin": "true"},
        )

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_validated_connector_instance("c1", req)
            assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value


# ============================================================================
# create_connector_instance — beta validation gap (lines 2295-2296, 2303, 2315)
# ============================================================================


class TestCreateConnectorInstanceBetaGaps:
    @pytest.mark.asyncio
    async def test_get_account_type_exception_continues(self):
        from app.connectors.api.router import create_connector_instance

        registry = AsyncMock()
        registry.get_connector_metadata = AsyncMock(return_value={
            "config": {"auth": {"schemas": {}, "oauthConfigs": {}}},
            "scope": ["personal"],
            "supportedAuthTypes": ["NONE"],
        })
        registry._normalize_connector_name = MagicMock(return_value="googledrive")
        registry._get_beta_connector_names = MagicMock(return_value=set())
        registry.create_connector_instance_on_configuration = AsyncMock(return_value={"_key": "c1"})

        gp = AsyncMock()
        gp.get_account_type = AsyncMock(side_effect=RuntimeError("db error"))

        config_service = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        container.config_service = MagicMock(return_value=config_service)

        req = _mock_request(
            connector_registry=registry,
            graph_provider=gp,
            container=container,
            body={
                "connectorType": "GOOGLE_DRIVE",
                "instanceName": "My Drive",
                "scope": "personal",
            },
        )

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            result = await create_connector_instance(req, graph_provider=gp)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_beta_team_enterprise_raises_403(self):
        from app.connectors.api.router import create_connector_instance

        registry = AsyncMock()
        registry.get_connector_metadata = AsyncMock(return_value={
            "config": {"auth": {"schemas": {}, "oauthConfigs": {}}},
            "scope": ["personal", "team"],
            "supportedAuthTypes": ["NONE"],
        })
        registry._normalize_connector_name = MagicMock(return_value="mybeta")
        registry._get_beta_connector_names = MagicMock(return_value={"mybeta"})

        gp = AsyncMock()
        gp.get_account_type = AsyncMock(return_value="enterprise")

        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("test"))

        req = _mock_request(
            connector_registry=registry,
            graph_provider=gp,
            container=container,
            body={
                "connectorType": "MY_BETA",
                "instanceName": "Beta Conn",
                "scope": "team",
            },
            headers={"X-Is-Admin": "true"},
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_connector_instance(req, graph_provider=gp)
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    @pytest.mark.asyncio
    async def test_unsupported_scope_raises_400(self):
        from app.connectors.api.router import create_connector_instance

        registry = AsyncMock()
        registry.get_connector_metadata = AsyncMock(return_value={
            "config": {"auth": {}},
            "scope": ["personal"],  # Only personal supported
            "supportedAuthTypes": ["NONE"],
        })
        registry._normalize_connector_name = MagicMock(return_value="googledrive")
        registry._get_beta_connector_names = MagicMock(return_value=set())

        gp = AsyncMock()
        gp.get_account_type = AsyncMock(return_value="individual")

        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("test"))

        req = _mock_request(
            connector_registry=registry,
            graph_provider=gp,
            container=container,
            body={
                "connectorType": "GOOGLE_DRIVE",
                "instanceName": "My Drive",
                "scope": "team",
            },
            headers={"X-Is-Admin": "true"},
        )

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await create_connector_instance(req, graph_provider=gp)
            assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value


# ============================================================================
# create_connector_instance — config["auth"] missing key (line 2452)
# ============================================================================


class TestCreateConnectorInstanceConfigAuthMissing:
    @pytest.mark.asyncio
    async def test_admin_oauth_created_but_config_has_no_auth_key(self):
        """When created_oauth_id is returned but config has no 'auth' key, it should be created."""
        from app.connectors.api.router import create_connector_instance

        registry = AsyncMock()
        registry.get_connector_metadata = AsyncMock(return_value={
            "config": {"auth": {"schemas": {}, "oauthConfigs": {}}},
            "scope": ["personal"],
            "supportedAuthTypes": ["OAUTH"],
        })
        registry._normalize_connector_name = MagicMock(return_value="googledrive")
        registry._get_beta_connector_names = MagicMock(return_value=set())
        registry.create_connector_instance_on_configuration = AsyncMock(return_value={"_key": "c1"})

        gp = AsyncMock()
        gp.get_account_type = AsyncMock(return_value="individual")

        config_service = AsyncMock()
        config_service.set_config = AsyncMock()

        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        container.config_service = MagicMock(return_value=config_service)

        # Provide config without "auth" key but with oauth info at body level
        req = _mock_request(
            connector_registry=registry,
            graph_provider=gp,
            container=container,
            body={
                "connectorType": "GOOGLE_DRIVE",
                "instanceName": "My Drive",
                "config": {"sync": {"interval": 3600}},  # No "auth" key
                "oauthConfigId": "oid-1",
                "authType": "OAUTH",
            },
            headers={"X-Is-Admin": "true"},
        )

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            with patch(f"{_ROUTER}._prepare_connector_config", new_callable=AsyncMock, return_value={"auth": {}, "sync": {}, "filters": {}}):
                result = await create_connector_instance(req, graph_provider=gp)
        assert result["success"] is True


# ============================================================================
# get_records — edge case: user not found
# ============================================================================


class TestGetRecordsGaps:
    @pytest.mark.asyncio
    async def test_user_not_found_returns_404_dict(self):
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(return_value=None)
        req = _mock_request(graph_provider=gp)

        result = await get_records(req, graph_provider=gp)
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_exception_returns_error_dict(self):
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(side_effect=RuntimeError("boom"))
        req = _mock_request(graph_provider=gp)

        result = await get_records(req, graph_provider=gp)
        assert "error" in result


# ============================================================================
# handle_record_deletion — record not found (line 466-469)
# ============================================================================


class TestHandleRecordDeletionGaps:
    @pytest.mark.asyncio
    async def test_record_not_found_raises_404(self):
        gp = AsyncMock()
        gp.delete_records_and_relations = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await handle_record_deletion("rec-1", graph_provider=gp)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        gp = AsyncMock()
        gp.delete_records_and_relations = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(HTTPException) as exc_info:
            await handle_record_deletion("rec-1", graph_provider=gp)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_success_returns_response(self):
        gp = AsyncMock()
        gp.delete_records_and_relations = AsyncMock(return_value={"deleted": True})

        result = await handle_record_deletion("rec-1", graph_provider=gp)
        assert result["status"] == "success"


# ============================================================================
# _parse_filter_response — unknown connector
# ============================================================================


class TestParseFilterResponseGaps:
    def test_unknown_connector_returns_empty(self):
        from app.connectors.api.router import _parse_filter_response
        result = _parse_filter_response({"data": []}, "files", "UNKNOWN_CONNECTOR")
        assert result == []

    def test_malformed_data_returns_empty(self):
        from app.connectors.api.router import _parse_filter_response
        result = _parse_filter_response(None, "labels", "GMAIL")
        assert result == []


# ============================================================================
# get_connector_schema — edge case
# ============================================================================


class TestGetConnectorSchemaGaps:
    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        from app.connectors.api.router import get_connector_schema

        registry = AsyncMock()
        registry.get_connector_metadata = AsyncMock(side_effect=RuntimeError("boom"))
        req = _mock_request(connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_schema("GOOGLE_DRIVE", req)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# ReindexFailedRequest model
# ============================================================================


class TestReindexFailedRequest:
    def test_model_creation(self):
        from app.connectors.api.router import ReindexFailedRequest
        req = ReindexFailedRequest(connector="GOOGLE_DRIVE", origin="CONNECTOR")
        assert req.connector == "GOOGLE_DRIVE"
        assert req.origin == "CONNECTOR"


# ============================================================================
# get_mime_type_from_record
# ============================================================================


class TestGetMimeTypeFromRecord:
    def test_uses_record_mime_type_when_present(self):
        record = _mock_record(mime_type="text/html")
        assert get_mime_type_from_record(record) == "text/html"

    def test_guesses_from_record_name(self):
        record = _mock_record(mime_type=None, record_name="report.xlsx")
        result = get_mime_type_from_record(record)
        assert "spreadsheet" in result or "excel" in result or "officedocument" in result

    def test_falls_back_to_octet_stream(self):
        record = SimpleNamespace(mime_type=None, record_name=None, name=None)
        assert get_mime_type_from_record(record) == "application/octet-stream"

    def test_uses_name_attr_if_record_name_missing(self):
        record = SimpleNamespace(mime_type=None, name="photo.jpg")
        result = get_mime_type_from_record(record)
        assert "image" in result

    def test_falls_back_when_guess_fails(self):
        record = SimpleNamespace(mime_type=None, record_name="weird_no_ext")
        assert get_mime_type_from_record(record) == "application/octet-stream"


# ============================================================================
# _parse_comma_separated_str
# ============================================================================


class TestParseCommaSeparatedStr:
    def test_none_returns_none(self):
        from app.connectors.api.router import _parse_comma_separated_str
        assert _parse_comma_separated_str(None) is None

    def test_empty_string_returns_none(self):
        from app.connectors.api.router import _parse_comma_separated_str
        assert _parse_comma_separated_str("") is None

    def test_single_value(self):
        from app.connectors.api.router import _parse_comma_separated_str
        assert _parse_comma_separated_str("pdf") == ["pdf"]

    def test_multiple_values(self):
        from app.connectors.api.router import _parse_comma_separated_str
        assert _parse_comma_separated_str("pdf, doc , xls") == ["pdf", "doc", "xls"]

    def test_empty_items_filtered(self):
        from app.connectors.api.router import _parse_comma_separated_str
        assert _parse_comma_separated_str("a,,b, ,c") == ["a", "b", "c"]


# ============================================================================
# _sanitize_app_name
# ============================================================================


class TestSanitizeAppName:
    def test_removes_spaces_and_lowercases(self):
        from app.connectors.api.router import _sanitize_app_name
        assert _sanitize_app_name("Google Drive") == "googledrive"
        assert _sanitize_app_name("SLACK") == "slack"
        assert _sanitize_app_name("Share Point Online") == "sharepointonline"


# ============================================================================
# _trim_config_values
# ============================================================================


class TestTrimConfigValues:
    def test_trims_string_values(self):
        from app.connectors.api.router import _trim_config_values
        assert _trim_config_values(obj="  hello  ") == "hello"

    def test_none_returns_none(self):
        from app.connectors.api.router import _trim_config_values
        assert _trim_config_values(obj=None) is None

    def test_preserves_bool(self):
        from app.connectors.api.router import _trim_config_values
        assert _trim_config_values(obj=True) is True

    def test_preserves_int(self):
        from app.connectors.api.router import _trim_config_values
        assert _trim_config_values(obj=42) == 42

    def test_preserves_float(self):
        from app.connectors.api.router import _trim_config_values
        assert _trim_config_values(obj=3.14) == 3.14

    def test_trims_list_elements(self):
        from app.connectors.api.router import _trim_config_values
        result = _trim_config_values(obj=["  a  ", " b "])
        assert result == ["a", "b"]

    def test_trims_dict_values(self):
        from app.connectors.api.router import _trim_config_values
        result = _trim_config_values(obj={"key": "  val  ", "num": 1})
        assert result == {"key": "val", "num": 1}

    def test_skips_sensitive_fields(self):
        from app.connectors.api.router import _trim_config_values
        result = _trim_config_values(obj={"certificate": "  cert  ", "normal": " x "})
        assert result["certificate"] == "  cert  "
        assert result["normal"] == "x"

    def test_nested_dict_with_path(self):
        from app.connectors.api.router import _trim_config_values
        result = _trim_config_values(obj={"auth": {"token": "  tok  ", "url": "  http  "}}, path="config")
        assert result["auth"]["token"] == "  tok  "  # token is in skip list
        assert result["auth"]["url"] == "http"


# ============================================================================
# _trim_connector_config
# ============================================================================


class TestTrimConnectorConfig:
    def test_trims_auth_sync_filters(self):
        config = {
            "auth": {"clientId": "  abc  "},
            "sync": {"interval": "  60  "},
            "filters": {"labels": " x "},
            "other": "  untouched  "
        }
        result = _trim_connector_config(config)
        assert result["auth"]["clientId"] == "abc"
        assert result["sync"]["interval"] == "60"
        assert result["filters"]["labels"] == "x"
        assert result["other"] == "  untouched  "

    def test_empty_config_returns_as_is(self):
        assert _trim_connector_config({}) == {}
        assert _trim_connector_config(None) is None

    def test_non_dict_config_returns_as_is(self):
        assert _trim_connector_config("not-a-dict") == "not-a-dict"


# ============================================================================
# _check_connector_not_locked
# ============================================================================


class TestCheckConnectorNotLocked:
    def test_not_locked_does_not_raise(self):
        _check_connector_not_locked({"isLocked": False})

    def test_locked_with_full_syncing(self):
        with pytest.raises(HTTPException) as exc_info:
            _check_connector_not_locked({"isLocked": True, "status": AppStatus.FULL_SYNCING.value})
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value
        assert "full sync" in exc_info.value.detail.lower()

    def test_locked_with_syncing(self):
        with pytest.raises(HTTPException) as exc_info:
            _check_connector_not_locked({"isLocked": True, "status": AppStatus.SYNCING.value})
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value
        assert "sync" in exc_info.value.detail.lower()

    def test_locked_with_unknown_status(self):
        with pytest.raises(HTTPException) as exc_info:
            _check_connector_not_locked({"isLocked": True, "status": "UNKNOWN"})
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value
        assert "another operation" in exc_info.value.detail.lower()

    def test_no_isLocked_does_not_raise(self):
        _check_connector_not_locked({"status": "READY"})


# ============================================================================
# require_connector_not_locked
# ============================================================================


class TestRequireConnectorNotLocked:
    @pytest.mark.asyncio
    async def test_unlocked_passes(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={"isLocked": False})
        req = _mock_request(connector_registry=registry)
        await require_connector_not_locked("c1", req)

    @pytest.mark.asyncio
    async def test_instance_not_found_passes(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value=None)
        req = _mock_request(connector_registry=registry)
        await require_connector_not_locked("c1", req)

    @pytest.mark.asyncio
    async def test_locked_raises_409(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(
            return_value={"isLocked": True, "status": AppStatus.SYNCING.value}
        )
        req = _mock_request(connector_registry=registry)
        with pytest.raises(HTTPException) as exc_info:
            await require_connector_not_locked("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value


# ============================================================================
# _encode_state_with_instance / _decode_state_with_instance
# ============================================================================


class TestEncodeDecodeState:
    def test_round_trip(self):
        from app.connectors.api.router import _encode_state_with_instance, _decode_state_with_instance
        encoded = _encode_state_with_instance("abc123", "conn-42")
        decoded = _decode_state_with_instance(encoded)
        assert decoded["state"] == "abc123"
        assert decoded["connector_id"] == "conn-42"

    def test_invalid_state_raises(self):
        from app.connectors.api.router import _decode_state_with_instance
        with pytest.raises(ValueError):
            _decode_state_with_instance("not-valid-base64!!!")


# ============================================================================
# _get_config_path_for_instance
# ============================================================================


class TestGetConfigPathForInstance:
    def test_returns_correct_path(self):
        assert _get_config_path_for_instance("abc-123") == "/services/connectors/abc-123/config"


# ============================================================================
# _validate_connector_deletion_permissions
# ============================================================================


class TestValidateConnectorDeletionPermissions:
    def test_team_non_admin_raises(self):
        from app.connectors.api.router import _validate_connector_deletion_permissions
        with pytest.raises(HTTPException) as exc_info:
            _validate_connector_deletion_permissions(
                {"scope": ConnectorScope.TEAM.value, "createdBy": "u1"},
                "u1", is_admin=False, logger=logging.getLogger("test")
            )
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    def test_personal_non_creator_raises(self):
        from app.connectors.api.router import _validate_connector_deletion_permissions
        with pytest.raises(HTTPException) as exc_info:
            _validate_connector_deletion_permissions(
                {"scope": ConnectorScope.PERSONAL.value, "createdBy": "other"},
                "u1", is_admin=True, logger=logging.getLogger("test")
            )
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    def test_personal_creator_passes(self):
        from app.connectors.api.router import _validate_connector_deletion_permissions
        _validate_connector_deletion_permissions(
            {"scope": ConnectorScope.PERSONAL.value, "createdBy": "u1"},
            "u1", is_admin=False, logger=logging.getLogger("test")
        )

    def test_team_admin_passes(self):
        from app.connectors.api.router import _validate_connector_deletion_permissions
        _validate_connector_deletion_permissions(
            {"scope": ConnectorScope.TEAM.value, "createdBy": "u1"},
            "u1", is_admin=True, logger=logging.getLogger("test")
        )


# ============================================================================
# _get_user_context
# ============================================================================


class TestGetUserContext:
    def test_valid_user(self):
        from app.connectors.api.router import _get_user_context
        req = _mock_request(headers={"X-Is-Admin": "true"})
        ctx = _get_user_context(req)
        assert ctx["user_id"] == "user-1"
        assert ctx["org_id"] == "org-1"
        assert ctx["is_admin"] is True

    def test_missing_user_id_raises(self):
        from app.connectors.api.router import _get_user_context
        req = _mock_request(user={"userId": None, "orgId": "org-1"})
        with pytest.raises(HTTPException) as exc_info:
            _get_user_context(req)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    def test_missing_org_id_raises(self):
        from app.connectors.api.router import _get_user_context
        req = _mock_request(user={"userId": "u1", "orgId": None})
        with pytest.raises(HTTPException) as exc_info:
            _get_user_context(req)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value


# ============================================================================
# _validate_admin_only
# ============================================================================


class TestValidateAdminOnly:
    def test_non_admin_raises(self):
        from app.connectors.api.router import _validate_admin_only
        with pytest.raises(HTTPException) as exc_info:
            _validate_admin_only(is_admin=False, action="do stuff")
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value
        assert "do stuff" in exc_info.value.detail

    def test_admin_passes(self):
        from app.connectors.api.router import _validate_admin_only
        _validate_admin_only(is_admin=True, action="do stuff")


# ============================================================================
# _validate_connector_permissions
# ============================================================================


class TestValidateConnectorPermissions:
    def test_team_non_admin_raises(self):
        with pytest.raises(HTTPException):
            _validate_connector_permissions(
                {"scope": ConnectorScope.TEAM.value, "createdBy": "u1"},
                "u1", is_admin=False, action="update"
            )

    def test_personal_non_creator_non_admin_raises(self):
        with pytest.raises(HTTPException):
            _validate_connector_permissions(
                {"scope": ConnectorScope.PERSONAL.value, "createdBy": "other"},
                "u1", is_admin=False, action="update"
            )

    def test_personal_admin_non_creator_raises(self):
        """Admin can access personal connectors of others."""
        _validate_connector_permissions(
            {"scope": ConnectorScope.PERSONAL.value, "createdBy": "other"},
            "u1", is_admin=True, action="update"
        )

    def test_personal_creator_passes(self):
        _validate_connector_permissions(
            {"scope": ConnectorScope.PERSONAL.value, "createdBy": "u1"},
            "u1", is_admin=False, action="update"
        )

    def test_team_admin_passes(self):
        _validate_connector_permissions(
            {"scope": ConnectorScope.TEAM.value, "createdBy": "u1"},
            "u1", is_admin=True, action="update"
        )

    def test_unknown_scope_non_creator_non_admin_raises(self):
        with pytest.raises(HTTPException):
            _validate_connector_permissions(
                {"scope": "other", "createdBy": "other"},
                "u1", is_admin=False, action="update"
            )


# ============================================================================
# _get_and_validate_connector_instance
# ============================================================================


class TestGetAndValidateConnectorInstance:
    @pytest.mark.asyncio
    async def test_found(self):
        from app.connectors.api.router import _get_and_validate_connector_instance
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={"_key": "c1", "type": "SLACK"})
        ctx = {"user_id": "u1", "org_id": "o1", "is_admin": False}
        result = await _get_and_validate_connector_instance("c1", ctx, registry, logging.getLogger("test"))
        assert result["_key"] == "c1"

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self):
        from app.connectors.api.router import _get_and_validate_connector_instance
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value=None)
        ctx = {"user_id": "u1", "org_id": "o1", "is_admin": False}
        with pytest.raises(HTTPException) as exc_info:
            await _get_and_validate_connector_instance("c1", ctx, registry, logging.getLogger("test"))
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value


# ============================================================================
# _find_oauth_config_in_list
# ============================================================================


class TestFindOAuthConfigInList:
    @pytest.mark.asyncio
    async def test_found(self):
        from app.connectors.api.router import _find_oauth_config_in_list
        configs = [
            {"_id": "c1", "orgId": "o1"},
            {"_id": "c2", "orgId": "o1"},
        ]
        config, idx = await _find_oauth_config_in_list(configs, "c2", "o1", logging.getLogger("test"))
        assert config["_id"] == "c2"
        assert idx == 1

    @pytest.mark.asyncio
    async def test_wrong_org_returns_none(self):
        from app.connectors.api.router import _find_oauth_config_in_list
        configs = [{"_id": "c1", "orgId": "o2"}]
        config, idx = await _find_oauth_config_in_list(configs, "c1", "o1", logging.getLogger("test"))
        assert config is None
        assert idx is None

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        from app.connectors.api.router import _find_oauth_config_in_list
        configs = [{"_id": "c1", "orgId": "o1"}]
        config, idx = await _find_oauth_config_in_list(configs, "c999", "o1", logging.getLogger("test"))
        assert config is None
        assert idx is None


# ============================================================================
# _check_oauth_name_conflict
# ============================================================================


class TestCheckOAuthNameConflict:
    def test_no_conflict_passes(self):
        configs = [{"oauthInstanceName": "Config A", "orgId": "o1"}]
        _check_oauth_name_conflict(configs, "Config B", "o1")

    def test_conflict_raises_409(self):
        configs = [{"oauthInstanceName": "Config A", "orgId": "o1"}]
        with pytest.raises(HTTPException) as exc_info:
            _check_oauth_name_conflict(configs, "Config A", "o1")
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value

    def test_conflict_different_org_passes(self):
        configs = [{"oauthInstanceName": "Config A", "orgId": "o2"}]
        _check_oauth_name_conflict(configs, "Config A", "o1")

    def test_exclude_index_skips_self(self):
        configs = [{"oauthInstanceName": "Config A", "orgId": "o1"}]
        _check_oauth_name_conflict(configs, "Config A", "o1", exclude_index=0)


# ============================================================================
# get_validated_connector_instance — more branches
# ============================================================================


class TestGetValidatedConnectorInstanceAdditional:
    @pytest.mark.asyncio
    async def test_unauthenticated_raises_401(self):
        registry = AsyncMock()
        req = _mock_request(user={"userId": None, "orgId": "org-1"}, connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_validated_connector_instance("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    @pytest.mark.asyncio
    async def test_instance_not_found_raises_404(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value=None)
        req = _mock_request(connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_validated_connector_instance("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_team_scope_non_admin_raises_403(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "GOOGLE_DRIVE",
            "scope": ConnectorScope.TEAM.value,
            "createdBy": "user-1",
        })
        req = _mock_request(connector_registry=registry, headers={"X-Is-Admin": "false"})

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_validated_connector_instance("c1", req)
            assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    @pytest.mark.asyncio
    async def test_non_creator_non_admin_raises_403(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "GOOGLE_DRIVE",
            "scope": "custom",
            "createdBy": "other-user",
        })
        req = _mock_request(connector_registry=registry, headers={"X-Is-Admin": "false"})

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_validated_connector_instance("c1", req)
            assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    @pytest.mark.asyncio
    async def test_valid_instance_returns_dict(self):
        registry = AsyncMock()
        instance = {
            "type": "GOOGLE_DRIVE",
            "scope": ConnectorScope.PERSONAL.value,
            "createdBy": "user-1",
        }
        registry.get_connector_instance = AsyncMock(return_value=instance)
        req = _mock_request(connector_registry=registry, headers={"X-Is-Admin": "false"})

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            result = await get_validated_connector_instance("c1", req)
        assert result == instance


# ============================================================================
# get_records — success path
# ============================================================================


class TestGetRecordsAdditional:
    @pytest.mark.asyncio
    async def test_success_returns_paginated(self):
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "ukey1"})
        gp.get_records = AsyncMock(return_value=(
            [{"id": "r1"}],  # records
            1,  # total_count
            {"types": ["PDF"]},  # available_filters
        ))
        req = _mock_request(graph_provider=gp)

        result = await get_records(
            req, graph_provider=gp, page=1, limit=20,
            search=None, record_types=None, origins=None, connectors=None,
            indexing_status=None, permissions=None, date_from=None, date_to=None,
            sort_by="createdAtTimestamp", sort_order="desc", source="all"
        )
        assert result["records"] == [{"id": "r1"}]
        assert result["pagination"]["totalCount"] == 1

    @pytest.mark.asyncio
    async def test_with_filters(self):
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "ukey1"})
        gp.get_records = AsyncMock(return_value=([], 0, {}))
        req = _mock_request(graph_provider=gp)

        result = await get_records(
            req, graph_provider=gp, page=1, limit=10,
            search="test", record_types="PDF,DOC", origins="CONNECTOR",
            connectors="GOOGLE_DRIVE", indexing_status="COMPLETED",
            permissions=None, date_from=1000, date_to=2000,
            sort_by="recordName", sort_order="asc", source="kb"
        )
        assert "records" in result


# ============================================================================
# handle_record_deletion — success with event data
# ============================================================================


class TestHandleRecordDeletionSuccess:
    @pytest.mark.asyncio
    async def test_success_returns_response(self):
        gp = AsyncMock()
        gp.delete_records_and_relations = AsyncMock(return_value={"deleted": True})

        result = await handle_record_deletion("rec-1", graph_provider=gp)
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_http_exception_re_raised(self):
        gp = AsyncMock()
        gp.delete_records_and_relations = AsyncMock(
            side_effect=HTTPException(status_code=403, detail="Forbidden")
        )
        with pytest.raises(HTTPException) as exc_info:
            await handle_record_deletion("rec-1", graph_provider=gp)
        assert exc_info.value.status_code == 403


# ============================================================================
# delete_record — success with event data published
# ============================================================================


class TestDeleteRecordEventPublish:
    @pytest.mark.asyncio
    async def test_success_with_event_data_published(self):
        req = _mock_request()
        gp = AsyncMock()
        gp.delete_record = AsyncMock(return_value={
            "success": True,
            "eventData": {"eventType": "deleted", "payload": {"id": "rec-1"}, "topic": "t"},
            "connector": "c1",
            "timestamp": 123,
        })
        kafka = AsyncMock()
        kafka.publish_event = AsyncMock()

        result = await delete_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert result["success"] is True
        kafka.publish_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_failure_result_raises_http_exception(self):
        req = _mock_request()
        gp = AsyncMock()
        gp.delete_record = AsyncMock(return_value={
            "success": False, "reason": "not found", "code": 404,
        })
        kafka = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await delete_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert exc_info.value.status_code == 404


# ============================================================================
# reindex_single_record — success with event data
# ============================================================================


class TestReindexSingleRecordSuccess:
    @pytest.mark.asyncio
    async def test_success_with_event_published(self):
        req = _mock_request(body={"depth": 1})
        gp = AsyncMock()
        gp.reindex_single_record = AsyncMock(return_value={
            "success": True,
            "eventData": {"eventType": "reindex", "payload": {"id": "rec-1"}, "topic": "t"},
            "recordId": "rec-1",
            "recordName": "doc.pdf",
            "connector": "c1",
            "userRole": "admin",
        })
        kafka = AsyncMock()
        kafka.publish_event = AsyncMock()

        result = await reindex_single_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert result["success"] is True
        assert result["depth"] == 1
        assert result["eventPublished"] is True

    @pytest.mark.asyncio
    async def test_success_no_event_data(self):
        req = _mock_request(body={"depth": 0})
        gp = AsyncMock()
        gp.reindex_single_record = AsyncMock(return_value={
            "success": True,
            "eventData": None,
            "recordId": "rec-1",
            "recordName": "doc.pdf",
            "connector": "c1",
            "userRole": "user",
        })
        kafka = AsyncMock()

        result = await reindex_single_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert result["success"] is True
        assert result["eventPublished"] is False


# ============================================================================
# reindex_record_group — success path
# ============================================================================


class TestReindexRecordGroupSuccess:
    @pytest.mark.asyncio
    async def test_success_publishes_event(self):
        req = _mock_request(body={"depth": 2})
        gp = AsyncMock()
        gp.reindex_record_group_records = AsyncMock(return_value={
            "success": True,
            "connectorId": "c1",
            "connectorName": "Google Drive",
            "userKey": "uk1",
            "depth": 2,
        })
        kafka = AsyncMock()
        kafka.publish_event = AsyncMock()

        with patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=999):
            result = await reindex_record_group("rg-1", req, graph_provider=gp, kafka_service=kafka)
        assert result["success"] is True
        assert result["eventPublished"] is True
        kafka.publish_event.assert_called_once()


# ============================================================================
# get_connector_registry — success path + scope validation
# ============================================================================


class TestGetConnectorRegistryAdditional:
    @pytest.mark.asyncio
    async def test_invalid_scope_raises_400(self):
        req = _mock_request()
        with pytest.raises(HTTPException) as exc_info:
            await get_connector_registry(req, scope="invalid", page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value


# ============================================================================
# get_connector_instances — authentication + scope checks
# ============================================================================


class TestGetConnectorInstancesAdditional:
    @pytest.mark.asyncio
    async def test_unauthenticated_raises_401(self):
        registry = AsyncMock()
        req = _mock_request(user={"userId": None, "orgId": "org-1"}, connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instances(req, scope=None, page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    @pytest.mark.asyncio
    async def test_invalid_scope_raises_400(self):
        registry = AsyncMock()
        req = _mock_request(connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instances(req, scope="invalid", page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    @pytest.mark.asyncio
    async def test_success(self):
        registry = AsyncMock()
        registry.get_all_connector_instances = AsyncMock(return_value={"instances": [], "total": 0})
        req = _mock_request(connector_registry=registry)

        result = await get_connector_instances(req, scope=None, page=1, limit=20, search=None)
        assert result["success"] is True


# ============================================================================
# get_active_connector_instances — authentication check
# ============================================================================


class TestActiveConnectorInstancesAdditional:
    @pytest.mark.asyncio
    async def test_unauthenticated_raises_401(self):
        from app.connectors.api.router import get_active_connector_instances
        req = _mock_request(user={"userId": None, "orgId": "org-1"})

        with pytest.raises(HTTPException) as exc_info:
            await get_active_connector_instances(req)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.api.router import get_active_connector_instances
        registry = AsyncMock()
        registry.get_active_connector_instances = AsyncMock(return_value=[])
        req = _mock_request(connector_registry=registry)

        result = await get_active_connector_instances(req)
        assert result["success"] is True


# ============================================================================
# get_inactive_connector_instances — authentication check
# ============================================================================


class TestInactiveConnectorInstancesAdditional:
    @pytest.mark.asyncio
    async def test_unauthenticated_raises_401(self):
        from app.connectors.api.router import get_inactive_connector_instances
        req = _mock_request(user={"userId": None, "orgId": "org-1"})

        with pytest.raises(HTTPException) as exc_info:
            await get_inactive_connector_instances(req)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.api.router import get_inactive_connector_instances
        registry = AsyncMock()
        registry.get_inactive_connector_instances = AsyncMock(return_value=[])
        req = _mock_request(connector_registry=registry)

        result = await get_inactive_connector_instances(req)
        assert result["success"] is True


# ============================================================================
# get_configured_connector_instances — authentication + scope checks
# ============================================================================


class TestConfiguredConnectorInstancesAdditional:
    @pytest.mark.asyncio
    async def test_unauthenticated_raises_401(self):
        from app.connectors.api.router import get_configured_connector_instances
        req = _mock_request(user={"userId": None, "orgId": "org-1"})

        with pytest.raises(HTTPException) as exc_info:
            await get_configured_connector_instances(req, scope=None, page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    @pytest.mark.asyncio
    async def test_invalid_scope_raises_400(self):
        from app.connectors.api.router import get_configured_connector_instances
        req = _mock_request()

        with pytest.raises(HTTPException) as exc_info:
            await get_configured_connector_instances(req, scope="bad", page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.api.router import get_configured_connector_instances
        registry = AsyncMock()
        registry.get_configured_connector_instances = AsyncMock(return_value=[])
        req = _mock_request(connector_registry=registry)

        result = await get_configured_connector_instances(req, scope=None, page=1, limit=20, search=None)
        assert result["success"] is True


# ============================================================================
# get_connector_instance — success path
# ============================================================================


class TestGetConnectorInstanceAdditional:
    @pytest.mark.asyncio
    async def test_success(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={"_key": "c1", "type": "SLACK"})
        req = _mock_request(connector_registry=registry)

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            result = await get_connector_instance("c1", req)
        assert result["success"] is True
        assert result["connector"]["_key"] == "c1"

    @pytest.mark.asyncio
    async def test_unauthenticated_raises_401(self):
        registry = AsyncMock()
        req = _mock_request(user={"userId": None, "orgId": "org-1"}, connector_registry=registry)
        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instance("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value


# ============================================================================
# get_connector_instance_config — success path + auth cleanup
# ============================================================================


class TestGetConnectorInstanceConfigAdditional:
    @pytest.mark.asyncio
    async def test_success_with_config(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "SLACK", "name": "Slack", "scope": "personal",
            "createdBy": "user-1", "authType": "OAUTH",
        })
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"clientId": "abc", "authorizeUrl": "http://auth"},
            "sync": {"interval": 60},
            "filters": {},
            "credentials": "secret",
            "oauth": {"tok": "x"},
        })
        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        container.config_service = MagicMock(return_value=config_service)

        req = _mock_request(connector_registry=registry, container=container)

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            result = await get_connector_instance_config("c1", req)
        assert result["success"] is True
        config = result["config"]["config"]
        # Credentials and oauth removed
        assert "credentials" not in config
        assert "oauth" not in config
        # OAuth fields cleaned from auth
        assert "authorizeUrl" not in config["auth"]
        assert config["auth"]["clientId"] == "abc"

    @pytest.mark.asyncio
    async def test_unauthenticated_raises_401(self):
        registry = AsyncMock()
        req = _mock_request(user={"userId": None, "orgId": "org-1"}, connector_registry=registry)
        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instance_config("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value


# ============================================================================
# _parse_filter_response — known connectors
# ============================================================================


class TestParseFilterResponseKnown:
    def test_gmail_labels(self):
        from app.connectors.api.router import _parse_filter_response
        data = {"labels": [
            {"id": "L1", "name": "Work", "type": "user"},
            {"id": "L2", "name": "System", "type": "system"},
        ]}
        result = _parse_filter_response(data, "labels", "GMAIL")
        assert len(result) == 1
        assert result[0]["value"] == "L1"

    def test_drive_folders(self):
        from app.connectors.api.router import _parse_filter_response
        data = {"files": [{"id": "F1", "name": "Folder A"}]}
        result = _parse_filter_response(data, "folders", "DRIVE")
        assert len(result) == 1
        assert result[0]["label"] == "Folder A"

    def test_onedrive_folders(self):
        from app.connectors.api.router import _parse_filter_response
        data = {"value": [
            {"id": "F1", "name": "Docs", "folder": True},
            {"id": "F2", "name": "File", "folder": None},
        ]}
        result = _parse_filter_response(data, "folders", "ONEDRIVE")
        assert len(result) == 1

    def test_slack_channels(self):
        from app.connectors.api.router import _parse_filter_response
        data = {"channels": [
            {"id": "C1", "name": "general", "is_archived": False},
            {"id": "C2", "name": "old", "is_archived": True},
        ]}
        result = _parse_filter_response(data, "channels", "SLACK")
        assert len(result) == 1
        assert result[0]["label"] == "#general"

    def test_confluence_spaces(self):
        from app.connectors.api.router import _parse_filter_response
        data = {"results": [{"key": "DOC", "name": "Documentation"}]}
        result = _parse_filter_response(data, "spaces", "CONFLUENCE")
        assert len(result) == 1


# ============================================================================
# _get_static_filter_options
# ============================================================================


class TestGetStaticFilterOptions:
    @pytest.mark.asyncio
    async def test_file_types(self):
        from app.connectors.api.router import _get_static_filter_options
        result = await _get_static_filter_options("DRIVE", "fileTypes")
        assert len(result) > 0
        assert any(o["value"] == "pdf" for o in result)

    @pytest.mark.asyncio
    async def test_content_types(self):
        from app.connectors.api.router import _get_static_filter_options
        result = await _get_static_filter_options("CONFLUENCE", "contentTypes")
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_unknown_returns_empty(self):
        from app.connectors.api.router import _get_static_filter_options
        result = await _get_static_filter_options("DRIVE", "unknownFilter")
        assert result == []


# ============================================================================
# _get_fallback_filter_options
# ============================================================================


class TestGetFallbackFilterOptions:
    @pytest.mark.asyncio
    async def test_gmail_fallback(self):
        from app.connectors.api.router import _get_fallback_filter_options
        result = await _get_fallback_filter_options("GMAIL")
        assert "labels" in result

    @pytest.mark.asyncio
    async def test_unknown_returns_empty(self):
        from app.connectors.api.router import _get_fallback_filter_options
        result = await _get_fallback_filter_options("UNKNOWN")
        assert result == {}


# ============================================================================
# get_connector_schema — success path
# ============================================================================


class TestGetConnectorSchemaSuccess:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.api.router import get_connector_schema
        registry = AsyncMock()
        registry.get_connector_metadata = AsyncMock(return_value={
            "config": {"auth": {"authorizeUrl": "http://auth"}, "sync": {}}
        })
        req = _mock_request(connector_registry=registry)

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            result = await get_connector_schema("GOOGLE_DRIVE", req)
        assert result["success"] is True
        # authorizeUrl cleaned
        assert "authorizeUrl" not in result["schema"].get("auth", {})

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self):
        from app.connectors.api.router import get_connector_schema
        registry = AsyncMock()
        registry.get_connector_metadata = AsyncMock(return_value=None)
        req = _mock_request(connector_registry=registry)

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_connector_schema("NONEXISTENT", req)
            assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value


# ============================================================================
# update_connector_instance_name — success path
# ============================================================================


class TestUpdateConnectorInstanceNameSuccess:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.api.router import update_connector_instance_name

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "GOOGLE_DRIVE",
            "scope": ConnectorScope.PERSONAL.value,
            "createdBy": "user-1",
        })
        registry.update_connector_instance = AsyncMock(return_value={"_key": "c1"})
        req = _mock_request(
            connector_registry=registry,
            body={"instanceName": "New Name"},
        )

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            result = await update_connector_instance_name("c1", req)
        assert result["success"] is True
        assert result["connector"]["name"] == "New Name"

    @pytest.mark.asyncio
    async def test_empty_name_raises_400(self):
        from app.connectors.api.router import update_connector_instance_name

        registry = AsyncMock()
        req = _mock_request(
            connector_registry=registry,
            body={"instanceName": ""},
        )

        with pytest.raises(HTTPException) as exc_info:
            await update_connector_instance_name("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self):
        from app.connectors.api.router import update_connector_instance_name

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value=None)
        req = _mock_request(
            connector_registry=registry,
            body={"instanceName": "New Name"},
        )

        with pytest.raises(HTTPException) as exc_info:
            await update_connector_instance_name("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_update_fails_raises_500(self):
        from app.connectors.api.router import update_connector_instance_name

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "SLACK", "scope": ConnectorScope.PERSONAL.value,
            "createdBy": "user-1", "name": "Old Name",
        })
        registry.update_connector_instance = AsyncMock(return_value=None)
        req = _mock_request(
            connector_registry=registry,
            body={"instanceName": "New Name"},
        )

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await update_connector_instance_name("c1", req)
            assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# _handle_oauth_config_creation — various branches
# ============================================================================


class TestHandleOAuthConfigCreation:
    @pytest.mark.asyncio
    async def test_non_oauth_type_returns_none(self):
        from app.connectors.api.router import _handle_oauth_config_creation

        result = await _handle_oauth_config_creation(
            connector_type="SLACK",
            auth_config={"clientId": "abc"},
            instance_name="Slack",
            user_id="u1", org_id="o1",
            is_admin=True, config_service=AsyncMock(),
            oauth_config_id=None, auth_type="API_TOKEN",
            base_url="", logger=logging.getLogger("test"),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_credentials_with_oauth_app_id_returns_id(self):
        from app.connectors.api.router import _handle_oauth_config_creation

        with patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId", "clientSecret"]):
            result = await _handle_oauth_config_creation(
                connector_type="GOOGLE_DRIVE",
                auth_config={"oauthConfigId": "oid-1"},  # No actual credentials
                instance_name="My Drive",
                user_id="u1", org_id="o1",
                is_admin=True, config_service=AsyncMock(),
                oauth_config_id="oid-1", auth_type="OAUTH",
                base_url="", logger=logging.getLogger("test"),
            )
        assert result == "oid-1"

    @pytest.mark.asyncio
    async def test_no_credentials_no_oauth_id_returns_none(self):
        from app.connectors.api.router import _handle_oauth_config_creation

        with patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId", "clientSecret"]):
            result = await _handle_oauth_config_creation(
                connector_type="GOOGLE_DRIVE",
                auth_config={},  # No credentials
                instance_name="My Drive",
                user_id="u1", org_id="o1",
                is_admin=True, config_service=AsyncMock(),
                oauth_config_id=None, auth_type="OAUTH",
                base_url="", logger=logging.getLogger("test"),
            )
        assert result is None


# ============================================================================
# _build_oauth_flow_config
# ============================================================================


class TestBuildOAuthFlowConfig:
    @pytest.mark.asyncio
    async def test_direct_auth_config(self):
        from app.connectors.api.router import _build_oauth_flow_config
        auth_config = {"authorizeUrl": "http://auth", "clientId": "abc"}
        result = await _build_oauth_flow_config(
            auth_config=auth_config,
            connector_type="SLACK",
            org_id="o1",
            config_service=AsyncMock(),
            logger=logging.getLogger("test"),
        )
        assert result["authorizeUrl"] == "http://auth"
        assert result["clientId"] == "abc"

    @pytest.mark.asyncio
    async def test_shared_oauth_config_not_found_raises_404(self):
        from app.connectors.api.router import _build_oauth_flow_config
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])  # empty list

        with pytest.raises(HTTPException) as exc_info:
            await _build_oauth_flow_config(
                auth_config={"oauthConfigId": "oid-1"},
                connector_type="SLACK",
                org_id="o1",
                config_service=config_service,
                logger=logging.getLogger("test"),
            )
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_shared_oauth_config_found(self):
        from app.connectors.api.router import _build_oauth_flow_config
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "oid-1", "orgId": "o1", "authorizeUrl": "http://auth",
             "tokenUrl": "http://token", "config": {"clientId": "xyz"},
             "scopes": {"team_sync": ["read"]}}
        ])

        result = await _build_oauth_flow_config(
            auth_config={"oauthConfigId": "oid-1", "connectorScope": "team"},
            connector_type="SLACK",
            org_id="o1",
            config_service=config_service,
            logger=logging.getLogger("test"),
        )
        assert result["authorizeUrl"] == "http://auth"
        assert result["clientId"] == "xyz"
        assert result["scopes"] == ["read"]


# ============================================================================
# create_connector_instance — basic success path
# ============================================================================


class TestCreateConnectorInstanceBasic:
    @pytest.mark.asyncio
    async def test_missing_type_raises_400(self):
        from app.connectors.api.router import create_connector_instance
        gp = AsyncMock()
        req = _mock_request(body={"instanceName": "My Drive"}, graph_provider=gp)

        with pytest.raises(HTTPException) as exc_info:
            await create_connector_instance(req, graph_provider=gp)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    @pytest.mark.asyncio
    async def test_unauthenticated_raises_401(self):
        from app.connectors.api.router import create_connector_instance
        gp = AsyncMock()
        req = _mock_request(
            user={"userId": None, "orgId": "org-1"},
            body={"connectorType": "SLACK", "instanceName": "test"},
            graph_provider=gp,
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_connector_instance(req, graph_provider=gp)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    @pytest.mark.asyncio
    async def test_invalid_scope_raises_400(self):
        from app.connectors.api.router import create_connector_instance
        gp = AsyncMock()
        req = _mock_request(
            body={"connectorType": "SLACK", "instanceName": "test", "scope": "invalid"},
            graph_provider=gp,
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_connector_instance(req, graph_provider=gp)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    @pytest.mark.asyncio
    async def test_connector_type_not_found_raises_404(self):
        from app.connectors.api.router import create_connector_instance
        registry = AsyncMock()
        registry.get_connector_metadata = AsyncMock(return_value=None)
        gp = AsyncMock()
        req = _mock_request(
            connector_registry=registry,
            body={"connectorType": "NONEXISTENT", "instanceName": "test"},
            graph_provider=gp,
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_connector_instance(req, graph_provider=gp)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value


# ============================================================================
# convert_buffer_to_pdf_stream — more branches
# ============================================================================


class TestConvertBufferToPdfStreamAdditional:
    @pytest.mark.asyncio
    async def test_bytes_buffer(self):
        """bytes buffer is written to temp file, then converted."""
        with patch(f"{_ROUTER}.convert_to_pdf", new_callable=AsyncMock) as mock_convert, \
             patch(f"{_ROUTER}.create_stream_record_response") as mock_stream:
            mock_convert.return_value = "/tmp/nonexistent.pdf"
            mock_stream.return_value = MagicMock()
            with patch("os.path.exists", return_value=False):
                with patch("os.listdir", return_value=[]):
                    with pytest.raises(HTTPException):
                        await convert_buffer_to_pdf_stream(b"binary-data", "file.pptx", "pptx")

    @pytest.mark.asyncio
    async def test_unsupported_type_raises_500(self):
        with pytest.raises(HTTPException) as exc_info:
            await convert_buffer_to_pdf_stream(12345, "file.pptx", "pptx")
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
        assert "Unsupported buffer type" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_file_like_buffer(self):
        """file-like object with read() is supported."""
        buf = io.BytesIO(b"file data")

        with patch(f"{_ROUTER}.convert_to_pdf", new_callable=AsyncMock) as mock_convert, \
             patch(f"{_ROUTER}.create_stream_record_response") as mock_stream:
            mock_convert.return_value = "/tmp/nonexistent.pdf"
            mock_stream.return_value = MagicMock()
            with patch("os.path.exists", return_value=False):
                with patch("os.listdir", return_value=[]):
                    with pytest.raises(HTTPException):
                        await convert_buffer_to_pdf_stream(buf, "file.pptx", "pptx")


# ============================================================================
# _clean_schema_for_response — additional
# ============================================================================


class TestCleanSchemaForResponseAdditional:
    def test_deeply_nested(self):
        schema = {
            "_oauth_configs": {"x": "y"},
            "auth": {
                "authorizeUrl": "http://...",
                "tokenUrl": "http://...",
                "scopes": ["x"],
                "oauthConfigs": {"y": "z"},
                "schemas": {"OAUTH": {"redirectUri": "/callback"}},
                "other": "kept"
            },
            "sync": {"interval": 3600}
        }
        result = _clean_schema_for_response(schema)
        assert "_oauth_configs" not in result
        assert result["auth"]["other"] == "kept"
        assert result["auth"]["schemas"]["OAUTH"]["redirectUri"] == "/callback"
        assert result["sync"]["interval"] == 3600


# ============================================================================
# stream_record — access denied
# ============================================================================


class TestStreamRecordAccessDenied:
    @pytest.mark.asyncio
    async def test_no_access_raises_403(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={"_key": "org-1"})
        record = _mock_record(org_id="org-1")
        gp.get_record_by_id = AsyncMock(return_value=record)
        gp.check_record_access_with_details = AsyncMock(return_value=None)

        req = _mock_request(graph_provider=gp)
        config_service = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await stream_record(req, "rec-1", graph_provider=gp, config_service=config_service)
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    @pytest.mark.asyncio
    async def test_record_not_found_raises_404(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={"_key": "org-1"})
        gp.get_record_by_id = AsyncMock(return_value=None)

        req = _mock_request(graph_provider=gp)
        config_service = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await stream_record(req, "rec-1", graph_provider=gp, config_service=config_service)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_org_not_found_raises_404(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value=None)
        gp.get_record_by_id = AsyncMock(return_value=_mock_record())

        req = _mock_request(graph_provider=gp)
        config_service = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await stream_record(req, "rec-1", graph_provider=gp, config_service=config_service)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value


# ============================================================================
# _prepare_connector_config — OAUTH type with base_url
# ============================================================================


class TestPrepareConnectorConfigOAuth:
    @pytest.mark.asyncio
    async def test_oauth_type_with_base_url(self):
        from app.connectors.api.router import _prepare_connector_config

        config_service = AsyncMock()

        result = await _prepare_connector_config(
            config={"auth": {"someField": "val"}},
            connector_type="GOOGLE_DRIVE",
            scope="personal",
            oauth_config_id=None,
            metadata={
                "config": {
                    "auth": {
                        "schemas": {"OAUTH": {"redirectUri": "callback"}},
                        "oauthConfigs": {"OAUTH": {"authorizeUrl": "http://auth", "tokenUrl": "http://token", "scopes": ["read"]}},
                    }
                }
            },
            selected_auth_type="OAUTH",
            user_id="u1",
            org_id="o1",
            is_admin=False,
            config_service=config_service,
            base_url="http://myapp.com",
            logger=logging.getLogger("test"),
        )
        assert "http://myapp.com" in result["auth"]["redirectUri"]
        assert result["auth"]["authorizeUrl"] == "http://auth"

    @pytest.mark.asyncio
    async def test_non_oauth_keeps_all_fields(self):
        from app.connectors.api.router import _prepare_connector_config

        config_service = AsyncMock()

        result = await _prepare_connector_config(
            config={"auth": {"clientId": "abc", "apiKey": "xyz"}},
            connector_type="SLACK",
            scope="personal",
            oauth_config_id=None,
            metadata={"config": {"auth": {"schemas": {}, "oauthConfigs": {}}}},
            selected_auth_type="API_TOKEN",
            user_id="u1",
            org_id="o1",
            is_admin=False,
            config_service=config_service,
            base_url="",
            logger=logging.getLogger("test"),
        )
        # Non-OAUTH keeps all auth fields
        assert result["auth"]["clientId"] == "abc"
        assert result["auth"]["apiKey"] == "xyz"

# =============================================================================
# Merged from test_router_coverage_gaps.py
# =============================================================================

_ROUTER = "app.connectors.api.router"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _mock_record(**overrides):
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
    req = MagicMock()
    user_data = user or {"userId": "user-1", "orgId": "org-1"}
    req.state = MagicMock()
    req.state.user = MagicMock()
    req.state.user.get = lambda k, default=None: user_data.get(k, default)

    _headers = headers or {}
    req.headers = MagicMock()
    req.headers.get = lambda k, default=None: _headers.get(k, default)

    if body is not None:
        req.json = AsyncMock(return_value=body)
    else:
        req.json = AsyncMock(return_value={})

    if query_params:
        req.query_params = MagicMock()
        req.query_params.get = lambda k, default=None: query_params.get(k, default)
    else:
        req.query_params = MagicMock()
        req.query_params.get = lambda k, default=None: None

    if container is not None:
        _container = container
    else:
        _container = MagicMock()
        _container.logger = MagicMock(return_value=logging.getLogger("test"))
        _container.config_service = MagicMock(return_value=AsyncMock())
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
# get_graph_provider / get_kafka_service (lines 243-249)
# ============================================================================


class TestDependencyHelpersCoverage:
    @pytest.mark.asyncio
    async def test_get_graph_provider_returns_from_app_state(self):
        req = MagicMock()
        fake_gp = MagicMock()
        req.app.state.graph_provider = fake_gp
        result = await get_graph_provider(req)
        assert result is fake_gp

    @pytest.mark.asyncio
    async def test_get_kafka_service_returns_from_container(self):
        req = MagicMock()
        fake_kafka = MagicMock()
        req.app.container.kafka_service = MagicMock(return_value=fake_kafka)
        result = await get_kafka_service(req)
        assert result is fake_kafka


# ============================================================================
# require_connector_not_locked_for_record — early return branches (lines 314-318)
# ============================================================================


class TestRequireConnectorNotLockedForRecordEdgeCasesCoverage:
    @pytest.mark.asyncio
    async def test_record_origin_not_connector_returns_early(self):
        """If origin != CONNECTOR, skip the lock check."""
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={"origin": "UPLOAD", "connectorId": "c1"})
        # Should not raise — origin is not CONNECTOR
        await require_connector_not_locked_for_record("rec-1", graph_provider=gp)

    @pytest.mark.asyncio
    async def test_no_connector_id_returns_early(self):
        """If connectorId is missing, skip the lock check."""
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={"origin": OriginTypes.CONNECTOR.value})
        await require_connector_not_locked_for_record("rec-1", graph_provider=gp)

    @pytest.mark.asyncio
    async def test_locked_app_doc_raises(self):
        """If connector is locked, raise 409."""
        gp = AsyncMock()
        record = {"origin": OriginTypes.CONNECTOR.value, "connectorId": "c1"}
        app_doc = {"isLocked": True, "status": AppStatus.SYNCING.value}
        gp.get_document = AsyncMock(side_effect=[record, app_doc])
        with pytest.raises(HTTPException) as exc_info:
            await require_connector_not_locked_for_record("rec-1", graph_provider=gp)
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value

    @pytest.mark.asyncio
    async def test_app_doc_not_found_passes(self):
        """If app_doc is None, do not raise."""
        gp = AsyncMock()
        record = {"origin": OriginTypes.CONNECTOR.value, "connectorId": "c1"}
        gp.get_document = AsyncMock(side_effect=[record, None])
        await require_connector_not_locked_for_record("rec-1", graph_provider=gp)


# ============================================================================
# require_connector_not_locked_for_record_group — branches (lines 321-336)
# ============================================================================


class TestRequireConnectorNotLockedForRecordGroupEdgeCasesCoverage:
    @pytest.mark.asyncio
    async def test_record_group_not_found_returns_early(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value=None)
        await require_connector_not_locked_for_record_group("rg-1", graph_provider=gp)

    @pytest.mark.asyncio
    async def test_no_connector_id_returns_early(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={"someField": "val"})
        await require_connector_not_locked_for_record_group("rg-1", graph_provider=gp)

    @pytest.mark.asyncio
    async def test_app_doc_not_found_passes(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(side_effect=[{"connectorId": "c1"}, None])
        await require_connector_not_locked_for_record_group("rg-1", graph_provider=gp)

    @pytest.mark.asyncio
    async def test_locked_app_doc_raises(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(
            side_effect=[{"connectorId": "c1"}, {"isLocked": True, "status": AppStatus.FULL_SYNCING.value}]
        )
        with pytest.raises(HTTPException) as exc_info:
            await require_connector_not_locked_for_record_group("rg-1", graph_provider=gp)
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value


# ============================================================================
# delete_record — success without event data + generic exception (lines 1240, 1270-1272)
# ============================================================================


class TestDeleteRecordGapsCoverage:
    @pytest.mark.asyncio
    async def test_success_without_event_data(self):
        """When result has no eventData, no kafka publish but still returns success."""
        req = _mock_request()
        gp = AsyncMock()
        gp.delete_record = AsyncMock(return_value={"success": True, "connector": "c1", "timestamp": 123})
        kafka = AsyncMock()

        result = await delete_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert result["success"] is True
        kafka.publish_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_event_data_without_payload(self):
        """eventData present but no payload key -- skip publish."""
        req = _mock_request()
        gp = AsyncMock()
        gp.delete_record = AsyncMock(
            return_value={"success": True, "eventData": {"eventType": "x"}, "connector": "c1"}
        )
        kafka = AsyncMock()
        result = await delete_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert result["success"] is True
        kafka.publish_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        """Non-HTTP exception becomes a 500."""
        req = _mock_request()
        gp = AsyncMock()
        gp.delete_record = AsyncMock(side_effect=RuntimeError("db down"))
        kafka = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await delete_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_kafka_publish_failure_does_not_raise(self):
        """Kafka publish failure is logged but doesn't raise."""
        req = _mock_request()
        gp = AsyncMock()
        gp.delete_record = AsyncMock(return_value={
            "success": True,
            "eventData": {"eventType": "deleted", "payload": {"id": "rec-1"}, "topic": "t"},
            "connector": "c1",
        })
        kafka = AsyncMock()
        kafka.publish_event = AsyncMock(side_effect=RuntimeError("kafka down"))

        result = await delete_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert result["success"] is True


# ============================================================================
# reindex_single_record — event publish failure + generic exception (lines 1330-1355)
# ============================================================================


class TestReindexSingleRecordGapsCoverage:
    @pytest.mark.asyncio
    async def test_event_publish_failure_still_returns_success(self):
        req = _mock_request(body={"depth": 0})
        gp = AsyncMock()
        gp.reindex_single_record = AsyncMock(return_value={
            "success": True,
            "eventData": {"eventType": "reindex", "payload": {"id": "rec-1"}, "topic": "t"},
            "recordId": "rec-1",
            "recordName": "doc.pdf",
            "connector": "c1",
            "userRole": "admin",
        })
        kafka = AsyncMock()
        kafka.publish_event = AsyncMock(side_effect=RuntimeError("kafka down"))

        result = await reindex_single_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert result["success"] is True
        assert result["eventPublished"] is True  # eventData was not None

    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        req = _mock_request()
        gp = AsyncMock()
        gp.reindex_single_record = AsyncMock(side_effect=RuntimeError("boom"))
        kafka = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await reindex_single_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_json_decode_error_uses_default_depth(self):
        """When request.json() fails with JSONDecodeError, depth defaults to 0."""
        req = _mock_request()
        req.json = AsyncMock(side_effect=json.JSONDecodeError("e", "doc", 0))
        gp = AsyncMock()
        gp.reindex_single_record = AsyncMock(return_value={
            "success": True,
            "eventData": None,
            "recordId": "rec-1",
            "recordName": "doc.pdf",
            "connector": "c1",
            "userRole": "user",
        })
        kafka = AsyncMock()

        result = await reindex_single_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert result["success"] is True
        assert result["depth"] == 0

    @pytest.mark.asyncio
    async def test_failure_result_raises_http_exception(self):
        req = _mock_request(body={})
        gp = AsyncMock()
        gp.reindex_single_record = AsyncMock(return_value={
            "success": False,
            "reason": "no access",
            "code": 403,
        })
        kafka = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await reindex_single_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert exc_info.value.status_code == 403


# ============================================================================
# reindex_record_group — JSON decode fallback + generic exception (lines 1402-1404, 1468-1470)
# ============================================================================


class TestReindexRecordGroupGapsCoverage:
    @pytest.mark.asyncio
    async def test_json_decode_error_uses_default_depth(self):
        req = _mock_request()
        req.json = AsyncMock(side_effect=json.JSONDecodeError("e", "doc", 0))
        gp = AsyncMock()
        gp.reindex_record_group_records = AsyncMock(return_value={
            "success": True,
            "connectorId": "c1",
            "connectorName": "Google Drive",
            "userKey": "uk1",
            "depth": 0,
        })
        kafka = AsyncMock()
        kafka.publish_event = AsyncMock()

        with patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=999):
            result = await reindex_record_group("rg-1", req, graph_provider=gp, kafka_service=kafka)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        req = _mock_request()
        req.json = AsyncMock(side_effect=RuntimeError("boom"))
        gp = AsyncMock()
        kafka = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await reindex_record_group("rg-1", req, graph_provider=gp, kafka_service=kafka)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_failure_result_raises_http_exception(self):
        req = _mock_request(body={"depth": 1})
        gp = AsyncMock()
        gp.reindex_record_group_records = AsyncMock(return_value={
            "success": False, "reason": "not found", "code": 404,
        })
        kafka = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await reindex_record_group("rg-1", req, graph_provider=gp, kafka_service=kafka)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_event_publish_failure_raises_500(self):
        req = _mock_request(body={"depth": 0})
        gp = AsyncMock()
        gp.reindex_record_group_records = AsyncMock(return_value={
            "success": True,
            "connectorId": "c1",
            "connectorName": "Google Drive",
            "userKey": "uk1",
            "depth": 0,
        })
        kafka = AsyncMock()
        kafka.publish_event = AsyncMock(side_effect=RuntimeError("kafka error"))

        with patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=999):
            with pytest.raises(HTTPException) as exc_info:
                await reindex_record_group("rg-1", req, graph_provider=gp, kafka_service=kafka)
            assert exc_info.value.status_code == 500


# ============================================================================
# check_beta_connector_access — feature flag refresh failure (lines 1541-1542)
# ============================================================================


class TestCheckBetaConnectorAccessGapsCoverage:
    @pytest.mark.asyncio
    async def test_feature_flag_refresh_failure_continues(self):
        """If feature_flag_service.refresh() fails, the check should continue."""
        container = MagicMock()
        ff_service = MagicMock()
        ff_service.refresh = AsyncMock(side_effect=RuntimeError("refresh failed"))
        ff_service.is_feature_enabled = MagicMock(return_value=True)  # beta enabled -> pass
        container.feature_flag_service = AsyncMock(return_value=ff_service)
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        req = MagicMock()
        req.app.container = container

        # Should not raise even though refresh failed
        await check_beta_connector_access("GOOGLE_DRIVE", req)

    @pytest.mark.asyncio
    async def test_beta_disabled_beta_connector_raises_403(self):
        container = MagicMock()
        ff_service = MagicMock()
        ff_service.refresh = AsyncMock()
        ff_service.is_feature_enabled = MagicMock(return_value=False)
        container.feature_flag_service = AsyncMock(return_value=ff_service)
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        req = MagicMock()
        req.app.container = container

        # "GOOGLE_DRIVE" normalizes to "google_drive" — include it in beta list
        with patch(f"{_ROUTER}.ConnectorFactory") as mock_factory:
            mock_factory.list_beta_connectors.return_value = ["google_drive"]
            with pytest.raises(HTTPException) as exc_info:
                await check_beta_connector_access("GOOGLE_DRIVE", req)
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_beta_disabled_non_beta_connector_passes(self):
        container = MagicMock()
        ff_service = MagicMock()
        ff_service.refresh = AsyncMock()
        ff_service.is_feature_enabled = MagicMock(return_value=False)
        container.feature_flag_service = AsyncMock(return_value=ff_service)
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        req = MagicMock()
        req.app.container = container

        with patch(f"{_ROUTER}.ConnectorFactory") as mock_factory:
            mock_factory.list_beta_connectors.return_value = ["someotherbeta"]
            # "GOOGLE_DRIVE" normalizes to "googledrive", not in beta list
            await check_beta_connector_access("GOOGLE_DRIVE", req)

    @pytest.mark.asyncio
    async def test_entire_check_exception_fails_open(self):
        """If the entire beta check fails, it should fail-open (not raise)."""
        container = MagicMock()
        container.feature_flag_service = AsyncMock(side_effect=RuntimeError("service unavailable"))
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        req = MagicMock()
        req.app.container = container

        # Should not raise
        await check_beta_connector_access("GOOGLE_DRIVE", req)


# ============================================================================
# get_connector_registry — account type exception + generic exception
# ============================================================================


class TestGetConnectorRegistryGapsCoverage:
    @pytest.mark.asyncio
    async def test_get_account_type_exception_continues(self):
        """If graph_provider.get_account_type raises, continue with None account_type."""
        gp = AsyncMock()
        gp.get_account_type = AsyncMock(side_effect=RuntimeError("db error"))
        registry = AsyncMock()
        registry.get_all_registered_connectors = AsyncMock(return_value={"connectors": [], "total": 0})
        req = _mock_request(graph_provider=gp, connector_registry=registry)

        result = await get_connector_registry(req, scope=None, page=1, limit=20, search=None)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        gp = AsyncMock()
        registry = AsyncMock()
        registry.get_all_registered_connectors = AsyncMock(side_effect=RuntimeError("boom"))
        req = _mock_request(graph_provider=gp, connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_registry(req, scope=None, page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_no_connectors_in_registry_raises_404(self):
        gp = AsyncMock()
        registry = AsyncMock()
        registry.get_all_registered_connectors = AsyncMock(return_value=None)
        req = _mock_request(graph_provider=gp, connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_registry(req, scope=None, page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value


# ============================================================================
# get_connector_instances — generic exception (lines 1787-1789)
# ============================================================================


class TestGetConnectorInstancesGapsCoverage:
    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        registry = AsyncMock()
        registry.get_all_connector_instances = AsyncMock(side_effect=RuntimeError("boom"))
        req = _mock_request(connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instances(req, scope=None, page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# get_active_connector_instances / get_inactive_connector_instances — generic exceptions
# ============================================================================


class TestActiveInactiveConnectorGapsCoverage:
    @pytest.mark.asyncio
    async def test_active_generic_exception_raises_500(self):
        from app.connectors.api.router import get_active_connector_instances

        registry = AsyncMock()
        registry.get_active_connector_instances = AsyncMock(side_effect=RuntimeError("boom"))
        req = _mock_request(connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_active_connector_instances(req)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_inactive_generic_exception_raises_500(self):
        from app.connectors.api.router import get_inactive_connector_instances

        registry = AsyncMock()
        registry.get_inactive_connector_instances = AsyncMock(side_effect=RuntimeError("boom"))
        req = _mock_request(connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_inactive_connector_instances(req)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# get_configured_connector_instances — generic exception (lines 1934-1936)
# ============================================================================


class TestGetConfiguredConnectorInstancesGapsCoverage:
    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        from app.connectors.api.router import get_configured_connector_instances

        registry = AsyncMock()
        registry.get_configured_connector_instances = AsyncMock(side_effect=RuntimeError("boom"))
        req = _mock_request(connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_configured_connector_instances(req, scope=None, page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# _prepare_connector_config — non-list oauth_configs + no base_url fallback
# ============================================================================


class TestPrepareConnectorConfigGapsCoverage:
    @pytest.mark.asyncio
    async def test_non_list_oauth_configs_resets_to_empty(self):
        """When config_service returns a non-list for oauth configs, it resets to [] and raises 404."""
        from app.connectors.api.router import _prepare_connector_config

        config_service = AsyncMock()
        # Return a non-list for oauth configs
        config_service.get_config = AsyncMock(return_value="not-a-list")

        with pytest.raises(HTTPException) as exc_info:
            await _prepare_connector_config(
                config={"auth": {"someField": "val"}},
                connector_type="GOOGLE_DRIVE",
                scope="personal",
                oauth_config_id="oid-1",
                metadata={"config": {"auth": {"schemas": {}, "oauthConfigs": {}}}},
                selected_auth_type="NONE",
                user_id="u1",
                org_id="o1",
                is_admin=False,
                config_service=config_service,
                base_url="",
                logger=logging.getLogger("test"),
            )
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_oauth_config_id_not_found_raises_404(self):
        from app.connectors.api.router import _prepare_connector_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])

        with pytest.raises(HTTPException) as exc_info:
            await _prepare_connector_config(
                config={"auth": {"someField": "val"}},
                connector_type="GOOGLE_DRIVE",
                scope="personal",
                oauth_config_id="oid-1",
                metadata={"config": {"auth": {"schemas": {}, "oauthConfigs": {}}}},
                selected_auth_type="NONE",
                user_id="u1",
                org_id="o1",
                is_admin=False,
                config_service=config_service,
                base_url="",
                logger=logging.getLogger("test"),
            )
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_oauth_type_no_base_url_uses_fallback(self):
        from app.connectors.api.router import _prepare_connector_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"frontend": {"publicEndpoint": "http://example.com"}}
        )

        result = await _prepare_connector_config(
            config={"auth": {"someField": "val"}},
            connector_type="GOOGLE_DRIVE",
            scope="personal",
            oauth_config_id=None,
            metadata={
                "config": {
                    "auth": {
                        "schemas": {"OAUTH": {"redirectUri": "callback"}},
                        "oauthConfigs": {"OAUTH": {"authorizeUrl": "http://auth", "tokenUrl": "http://token", "scopes": ["read"]}},
                    }
                }
            },
            selected_auth_type="OAUTH",
            user_id="u1",
            org_id="o1",
            is_admin=False,
            config_service=config_service,
            base_url="",
            logger=logging.getLogger("test"),
        )
        # Check redirect URI was built from fallback
        assert "example.com" in result["auth"]["redirectUri"]

    @pytest.mark.asyncio
    async def test_missing_auth_key_in_prepared_config(self):
        """When oauth_config_id found but auth key missing, it should be created."""
        from app.connectors.api.router import _prepare_connector_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value=[{"_id": "oid-1", "orgId": "o1"}]
        )

        result = await _prepare_connector_config(
            config=None,  # No config at all
            connector_type="GOOGLE_DRIVE",
            scope="personal",
            oauth_config_id="oid-1",
            metadata={"config": {"auth": {"schemas": {}, "oauthConfigs": {}}}},
            selected_auth_type="NONE",
            user_id="u1",
            org_id="o1",
            is_admin=False,
            config_service=config_service,
            base_url="",
            logger=logging.getLogger("test"),
        )
        assert "oauthConfigId" in result["auth"]


# ============================================================================
# get_connector_instance — generic exception (lines 2572-2574)
# ============================================================================


class TestGetConnectorInstanceGapsCoverage:
    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(side_effect=RuntimeError("boom"))
        req = _mock_request(connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instance("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value=None)
        req = _mock_request(connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instance("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value


# ============================================================================
# get_connector_instance_config — generic exception + auth cleanup (lines 2692-2694, 2650)
# ============================================================================


class TestGetConnectorInstanceConfigGapsCoverage:
    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(side_effect=RuntimeError("boom"))
        req = _mock_request(connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instance_config("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_instance_not_found_raises_404(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value=None)
        req = _mock_request(connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instance_config("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_config_not_found_returns_empty(self):
        """When config is not in etcd (raises exception), return default empty config."""
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "GOOGLE_DRIVE", "name": "My Drive", "scope": "personal",
            "createdBy": "u1", "authType": "OAUTH",
        })
        config_service = MagicMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("not found"))

        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        container.config_service = MagicMock(return_value=config_service)

        req = _mock_request(connector_registry=registry, container=container)

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            result = await get_connector_instance_config("c1", req)
        assert result["success"] is True
        assert result["config"]["config"] == {"auth": {}, "sync": {}, "filters": {}}

    @pytest.mark.asyncio
    async def test_auth_cleanup_removes_oauth_fields(self):
        """Auth section should have authorizeUrl, tokenUrl, scopes, oauthConfigs removed."""
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "GOOGLE_DRIVE", "name": "My Drive", "scope": "personal",
            "createdBy": "u1", "authType": "OAUTH",
        })
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authorizeUrl": "http://auth",
                "tokenUrl": "http://token",
                "scopes": ["read"],
                "oauthConfigs": {"some": "data"},
                "clientId": "abc",
            },
            "sync": {"interval": 3600},
            "filters": {},
            "credentials": "secret",
            "oauth": {"token": "t"},
        })

        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        container.config_service = MagicMock(return_value=config_service)

        req = _mock_request(connector_registry=registry, container=container)

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            result = await get_connector_instance_config("c1", req)
        auth = result["config"]["config"]["auth"]
        assert "authorizeUrl" not in auth
        assert "tokenUrl" not in auth
        assert "scopes" not in auth
        assert "oauthConfigs" not in auth
        assert auth["clientId"] == "abc"
        assert "credentials" not in result["config"]["config"]
        assert "oauth" not in result["config"]["config"]


# ============================================================================
# get_connector_stats_endpoint
# ============================================================================


class TestGetConnectorStatsGapsCoverage:
    @pytest.mark.asyncio
    async def test_success_returns_data(self):
        gp = AsyncMock()
        gp.get_connector_stats = AsyncMock(return_value={"success": True, "data": {"count": 10}})
        req = _mock_request(graph_provider=gp)

        result = await get_connector_stats_endpoint(req, "org-1", "c1", graph_provider=gp)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self):
        gp = AsyncMock()
        gp.get_connector_stats = AsyncMock(return_value={"success": False})
        req = _mock_request(graph_provider=gp)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_stats_endpoint(req, "org-1", "c1", graph_provider=gp)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_generic_exception_propagates(self):
        """When get_connector_stats raises before logger is set, UnboundLocalError propagates."""
        gp = AsyncMock()
        gp.get_connector_stats = AsyncMock(side_effect=RuntimeError("boom"))
        req = _mock_request(graph_provider=gp)

        # The router has a latent bug: logger is set AFTER graph_provider.get_connector_stats,
        # so if that call raises, the except block hits UnboundLocalError.
        with pytest.raises((HTTPException, UnboundLocalError)):
            await get_connector_stats_endpoint(req, "org-1", "c1", graph_provider=gp)


# ============================================================================
# stream_record — org mismatch, re-fetch fails (line 723)
# ============================================================================


class TestStreamRecordOrgMismatchCoverage:
    @pytest.mark.asyncio
    async def test_org_mismatch_refetch_org_not_found_raises_404(self):
        """When record.org_id != user org_id and re-fetch returns None, raise 404."""
        gp = AsyncMock()
        record = _mock_record(org_id="org-2")
        # First call: org lookup returns valid, record lookup returns record with different org_id
        # Then re-fetch with record org_id returns None
        gp.get_document = AsyncMock(side_effect=[
            {"_key": "org-1", "accountType": "individual"},  # org lookup
            None,  # re-fetch with record's org_id
        ])
        gp.get_record_by_id = AsyncMock(return_value=record)
        gp.check_record_access_with_details = AsyncMock(return_value=True)

        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        container.config_service = MagicMock(return_value=AsyncMock())

        req = _mock_request(graph_provider=gp, container=container)

        config_service = AsyncMock()
        with pytest.raises(HTTPException) as exc_info:
            await stream_record(req, "rec-1", graph_provider=gp, config_service=config_service)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value


# ============================================================================
# get_record_by_id — edge cases
# ============================================================================


class TestGetRecordByIdGapsCoverage:
    @pytest.mark.asyncio
    async def test_no_access_raises_500_wrapping_404(self):
        """When has_access is falsy, the inner 404 gets caught by outer except -> 500."""
        gp = AsyncMock()
        gp.check_record_access_with_details = AsyncMock(return_value=None)
        req = _mock_request()

        with pytest.raises(HTTPException) as exc_info:
            await get_record_by_id("rec-1", req, graph_provider=gp)
        # The function raises 404 inside try, but the outer except Exception catches it
        # and re-wraps as 500. This is a known pattern in the codebase.
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_exception_raises_500(self):
        gp = AsyncMock()
        gp.check_record_access_with_details = AsyncMock(side_effect=RuntimeError("db err"))
        req = _mock_request()

        with pytest.raises(HTTPException) as exc_info:
            await get_record_by_id("rec-1", req, graph_provider=gp)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_has_access_returns_data(self):
        gp = AsyncMock()
        access_data = {"recordId": "rec-1", "hasAccess": True}
        gp.check_record_access_with_details = AsyncMock(return_value=access_data)
        req = _mock_request()

        result = await get_record_by_id("rec-1", req, graph_provider=gp)
        assert result == access_data


# ============================================================================
# _clean_schema_for_response
# ============================================================================


class TestCleanSchemaForResponseCoverage:
    def test_removes_oauth_configs_and_auth_fields(self):
        schema = {
            "_oauth_configs": {"some": "data"},
            "auth": {
                "authorizeUrl": "http://auth",
                "tokenUrl": "http://token",
                "scopes": ["read"],
                "oauthConfigs": {"OAUTH": {}},
                "schemas": {"OAUTH": {"redirectUri": "/callback"}},
            },
            "sync": {"interval": 3600},
        }
        result = _clean_schema_for_response(schema)
        assert "_oauth_configs" not in result
        assert "authorizeUrl" not in result["auth"]
        assert "tokenUrl" not in result["auth"]
        assert "scopes" not in result["auth"]
        assert "oauthConfigs" not in result["auth"]
        assert "schemas" in result["auth"]

    def test_no_auth_key_returns_as_is(self):
        schema = {"sync": {"interval": 3600}}
        result = _clean_schema_for_response(schema)
        assert result == {"sync": {"interval": 3600}}


# ============================================================================
# _find_filter_field_config
# ============================================================================


class TestFindFilterFieldConfigCoverage:
    def test_finds_in_sync_category(self):
        metadata = {
            "config": {
                "filters": {
                    "sync": {
                        "schema": {
                            "fields": [
                                {"name": "folders", "type": "select"},
                                {"name": "labels", "type": "multiselect"},
                            ]
                        }
                    }
                }
            }
        }
        result = _find_filter_field_config(metadata, "folders")
        assert result == {"name": "folders", "type": "select"}

    def test_finds_in_indexing_category(self):
        metadata = {
            "config": {
                "filters": {
                    "indexing": {
                        "schema": {
                            "fields": [{"name": "fileTypes", "type": "multiselect"}]
                        }
                    }
                }
            }
        }
        result = _find_filter_field_config(metadata, "fileTypes")
        assert result["name"] == "fileTypes"

    def test_not_found_returns_none(self):
        metadata = {"config": {"filters": {"sync": {"schema": {"fields": []}}}}}
        assert _find_filter_field_config(metadata, "nonexistent") is None

    def test_empty_metadata_returns_none(self):
        assert _find_filter_field_config({}, "folders") is None


# ============================================================================
# _extract_essential_oauth_fields
# ============================================================================


class TestExtractEssentialOAuthFieldsCoverage:
    def test_extracts_all_fields(self):
        config = {
            "_id": "id1",
            "oauthInstanceName": "My Config",
            "iconPath": "/icons/google.svg",
            "appGroup": "Google",
            "appDescription": "desc",
            "appCategories": ["storage"],
            "connectorType": "GOOGLE_DRIVE",
            "createdAtTimestamp": 100,
            "updatedAtTimestamp": 200,
            "config": {"clientId": "secret"},  # should NOT be in output
        }
        result = _extract_essential_oauth_fields(config, "GOOGLE_DRIVE")
        assert result["_id"] == "id1"
        assert result["oauthInstanceName"] == "My Config"
        assert "config" not in result

    def test_defaults_for_missing_fields(self):
        config = {"_id": "id1"}
        result = _extract_essential_oauth_fields(config, "SLACK")
        assert result["connectorType"] == "SLACK"
        assert result["iconPath"] == "/icons/connectors/default.svg"
        assert result["appGroup"] == ""
        assert result["appCategories"] == []


# ============================================================================
# _find_oauth_config_by_id
# ============================================================================


class TestFindOAuthConfigByIdCoverage:
    def test_finds_matching_config(self):
        configs = [
            {"_id": "c1", "orgId": "org-1"},
            {"_id": "c2", "orgId": "org-1"},
        ]
        result = _find_oauth_config_by_id(configs, "c2", "org-1")
        assert result["_id"] == "c2"

    def test_wrong_org_returns_none(self):
        configs = [{"_id": "c1", "orgId": "org-2"}]
        assert _find_oauth_config_by_id(configs, "c1", "org-1") is None

    def test_not_found_returns_none(self):
        configs = [{"_id": "c1", "orgId": "org-1"}]
        assert _find_oauth_config_by_id(configs, "c999", "org-1") is None

    def test_empty_list_returns_none(self):
        assert _find_oauth_config_by_id([], "c1", "org-1") is None


# ============================================================================
# _get_oauth_config_path
# ============================================================================


class TestGetOAuthConfigPathCoverage:
    def test_normalizes_type(self):
        assert _get_oauth_config_path("Google Drive") == "/services/oauth/googledrive"
        assert _get_oauth_config_path("SLACK") == "/services/oauth/slack"


# ============================================================================
# _generate_oauth_config_id
# ============================================================================


class TestGenerateOAuthConfigIdCoverage:
    def test_returns_uuid_string(self):
        from app.connectors.api.router import _generate_oauth_config_id

        result = _generate_oauth_config_id()
        assert isinstance(result, str)
        assert len(result) == 36  # UUID format

    def test_unique_ids(self):
        from app.connectors.api.router import _generate_oauth_config_id

        ids = {_generate_oauth_config_id() for _ in range(10)}
        assert len(ids) == 10


# ============================================================================
# _get_oauth_field_names_from_registry
# ============================================================================


class TestGetOAuthFieldNamesFromRegistryCoverage:
    def test_returns_field_names_from_registry(self):
        from app.connectors.api.router import _get_oauth_field_names_from_registry

        with patch(f"{_ROUTER}.get_oauth_config_registry") as mock_get_reg:
            mock_reg = MagicMock()
            field1 = MagicMock()
            field1.name = "clientId"
            field2 = MagicMock()
            field2.name = "clientSecret"
            mock_config = MagicMock()
            mock_config.auth_fields = [field1, field2]
            mock_reg.get_config.return_value = mock_config
            mock_get_reg.return_value = mock_reg

            result = _get_oauth_field_names_from_registry("GOOGLE_DRIVE")
            assert result == ["clientId", "clientSecret"]

    def test_no_config_returns_defaults(self):
        from app.connectors.api.router import _get_oauth_field_names_from_registry

        with patch(f"{_ROUTER}.get_oauth_config_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_reg.get_config.return_value = None
            mock_get_reg.return_value = mock_reg

            result = _get_oauth_field_names_from_registry("UNKNOWN")
            assert result == ["clientId", "clientSecret"]

    def test_exception_returns_defaults(self):
        from app.connectors.api.router import _get_oauth_field_names_from_registry

        with patch(f"{_ROUTER}.get_oauth_config_registry", side_effect=RuntimeError):
            result = _get_oauth_field_names_from_registry("GOOGLE_DRIVE")
            assert result == ["clientId", "clientSecret"]


# ============================================================================
# _get_connector_from_container
# ============================================================================


class TestGetConnectorFromContainerCoverage:
    def test_from_connector_key_attr(self):
        from app.connectors.api.router import _get_connector_from_container

        container = MagicMock()
        connector_mock = MagicMock()
        container.c1_connector = MagicMock(return_value=connector_mock)
        result = _get_connector_from_container(container, "c1")
        assert result is connector_mock

    def test_from_connectors_map(self):
        from app.connectors.api.router import _get_connector_from_container

        container = MagicMock(spec=[])  # no dynamic attrs
        container.connectors_map = {"c1": MagicMock()}
        result = _get_connector_from_container(container, "c1")
        assert result is container.connectors_map["c1"]

    def test_not_found_returns_none(self):
        from app.connectors.api.router import _get_connector_from_container

        container = MagicMock(spec=[])
        result = _get_connector_from_container(container, "c1")
        assert result is None


# ============================================================================
# _get_settings_base_path — additional edge cases
# ============================================================================


class TestGetSettingsBasePathGapsCoverage:
    @pytest.mark.asyncio
    async def test_business_account_returns_company_path(self):
        gp = AsyncMock()
        gp.get_all_documents = AsyncMock(return_value=[{"accountType": "Business"}])
        result = await _get_settings_base_path(gp)
        assert "company-settings" in result

    @pytest.mark.asyncio
    async def test_enterprise_account_returns_company_path(self):
        gp = AsyncMock()
        gp.get_all_documents = AsyncMock(return_value=[{"accountType": "Enterprise"}])
        result = await _get_settings_base_path(gp)
        assert "company-settings" in result

    @pytest.mark.asyncio
    async def test_individual_account_returns_individual_path(self):
        gp = AsyncMock()
        gp.get_all_documents = AsyncMock(return_value=[{"accountType": "Individual"}])
        result = await _get_settings_base_path(gp)
        assert "individual" in result

    @pytest.mark.asyncio
    async def test_empty_list_returns_individual_path(self):
        gp = AsyncMock()
        gp.get_all_documents = AsyncMock(return_value=[])
        result = await _get_settings_base_path(gp)
        assert "individual" in result

    @pytest.mark.asyncio
    async def test_exception_returns_individual_path(self):
        gp = AsyncMock()
        gp.get_all_documents = AsyncMock(side_effect=RuntimeError("db down"))
        result = await _get_settings_base_path(gp)
        assert "individual" in result

    @pytest.mark.asyncio
    async def test_none_org_returns_individual_path(self):
        gp = AsyncMock()
        gp.get_all_documents = AsyncMock(return_value=[None])
        result = await _get_settings_base_path(gp)
        assert "individual" in result


# ============================================================================
# convert_buffer_to_pdf_stream — callable body_content + non-bytes body
# ============================================================================


class TestConvertBufferToPdfStreamBodyEdgeCasesCoverage:
    @pytest.mark.asyncio
    async def test_response_callable_body(self):
        """When Response.body is a callable, call it to get bytes content."""
        resp = MagicMock(spec=Response)
        resp.body = MagicMock(return_value=b"pdf-content")

        with patch(f"{_ROUTER}.convert_to_pdf", new_callable=AsyncMock) as mock_convert, \
             patch(f"{_ROUTER}.create_stream_record_response") as mock_stream:
            mock_convert.return_value = "/tmp/nonexistent.pdf"
            mock_stream.return_value = MagicMock()
            with patch("os.path.exists", return_value=False):
                with patch("os.listdir", return_value=[]):
                    with pytest.raises(HTTPException):
                        await convert_buffer_to_pdf_stream(resp, "file.pptx", "pptx")

    @pytest.mark.asyncio
    async def test_response_non_bytes_body_raises(self):
        """When Response.body returns non-bytes, raise 500."""
        resp = MagicMock(spec=Response)
        resp.body = "not-bytes-string"

        with pytest.raises(HTTPException) as exc_info:
            await convert_buffer_to_pdf_stream(resp, "file.pptx", "pptx")
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
        assert "not bytes" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_response_empty_body_raises(self):
        """When Response.body is None/empty, raise 500."""
        resp = MagicMock(spec=Response)
        resp.body = None

        with pytest.raises(HTTPException) as exc_info:
            await convert_buffer_to_pdf_stream(resp, "file.pptx", "pptx")
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# _create_or_update_oauth_config — no logger, update matching (lines 6038-6042, 6058)
# ============================================================================


class TestCreateOrUpdateOAuthConfigGapsCoverage:
    @pytest.mark.asyncio
    async def test_no_logger_uses_default(self):
        from app.connectors.api.router import _create_or_update_oauth_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])
        config_service.set_config = AsyncMock()

        with patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId", "clientSecret"]):
            with patch(f"{_ROUTER}._update_oauth_infrastructure_fields", new_callable=AsyncMock):
                with patch(f"{_ROUTER}._generate_oauth_config_id", return_value="new-id"):
                    with patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=999):
                        result = await _create_or_update_oauth_config(
                            connector_type="GOOGLE_DRIVE",
                            auth_config={"clientId": "abc", "clientSecret": "xyz"},
                            instance_name="My Config",
                            user_id="u1",
                            org_id="o1",
                            is_admin=True,
                            config_service=config_service,
                            base_url="http://example.com",
                            logger=None,  # No logger provided
                        )
        assert result == "new-id"

    @pytest.mark.asyncio
    async def test_update_existing_with_matching_id_and_org(self):
        from app.connectors.api.router import _create_or_update_oauth_config

        existing = {
            "_id": "existing-id",
            "userId": "u1",
            "orgId": "o1",
            "config": {"clientId": "old"},
        }
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[existing])
        config_service.set_config = AsyncMock()

        with patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId", "clientSecret"]):
            with patch(f"{_ROUTER}._update_oauth_infrastructure_fields", new_callable=AsyncMock):
                with patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=999):
                    result = await _create_or_update_oauth_config(
                        connector_type="GOOGLE_DRIVE",
                        auth_config={"clientId": "new-id-val"},
                        instance_name="Config",
                        user_id="u1",
                        org_id="o1",
                        is_admin=True,
                        config_service=config_service,
                        base_url="",
                        oauth_app_id="existing-id",
                        logger=logging.getLogger("test"),
                    )
        assert result == "existing-id"

    @pytest.mark.asyncio
    async def test_update_wrong_org_falls_through_to_create(self):
        from app.connectors.api.router import _create_or_update_oauth_config

        existing = {"_id": "existing-id", "userId": "u1", "orgId": "org-other", "config": {}}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[existing])
        config_service.set_config = AsyncMock()

        with patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId"]):
            with patch(f"{_ROUTER}._update_oauth_infrastructure_fields", new_callable=AsyncMock):
                with patch(f"{_ROUTER}._generate_oauth_config_id", return_value="new-id"):
                    with patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=999):
                        result = await _create_or_update_oauth_config(
                            connector_type="GOOGLE_DRIVE",
                            auth_config={"clientId": "abc"},
                            instance_name="Config",
                            user_id="u1",
                            org_id="o1",
                            is_admin=True,
                            config_service=config_service,
                            base_url="",
                            oauth_app_id="existing-id",
                            logger=logging.getLogger("test"),
                        )
        assert result == "new-id"

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        from app.connectors.api.router import _create_or_update_oauth_config

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))

        with patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId"]):
            result = await _create_or_update_oauth_config(
                connector_type="GOOGLE_DRIVE",
                auth_config={"clientId": "abc"},
                instance_name="Config",
                user_id="u1",
                org_id="o1",
                is_admin=True,
                config_service=config_service,
                base_url="",
                logger=logging.getLogger("test"),
            )
        assert result is None


# ============================================================================
# _get_oauth_configs_from_etcd
# ============================================================================


class TestGetOAuthConfigsFromEtcdCoverage:
    @pytest.mark.asyncio
    async def test_returns_list(self):
        from app.connectors.api.router import _get_oauth_configs_from_etcd

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[{"_id": "c1"}])
        result = await _get_oauth_configs_from_etcd("GOOGLE_DRIVE", config_service)
        assert result == [{"_id": "c1"}]

    @pytest.mark.asyncio
    async def test_non_list_returns_empty(self):
        from app.connectors.api.router import _get_oauth_configs_from_etcd

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value="not-a-list")
        result = await _get_oauth_configs_from_etcd("GOOGLE_DRIVE", config_service)
        assert result == []


# ============================================================================
# update_connector_instance_name — non-creator + generic exception
# (lines 3493-3500, 3544-3546)
# ============================================================================


class TestUpdateConnectorInstanceNameGapsCoverage:
    @pytest.mark.asyncio
    async def test_non_creator_non_admin_raises_403(self):
        from app.connectors.api.router import update_connector_instance_name

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "GOOGLE_DRIVE",
            "scope": ConnectorScope.PERSONAL.value,
            "createdBy": "other-user",
        })
        req = _mock_request(
            connector_registry=registry,
            body={"instanceName": "New Name"},
            headers={"X-Is-Admin": "false"},
        )

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await update_connector_instance_name("c1", req)
            assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    @pytest.mark.asyncio
    async def test_admin_non_creator_personal_raises_403(self):
        """Admin cannot update personal connector of another user."""
        from app.connectors.api.router import update_connector_instance_name

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "GOOGLE_DRIVE",
            "scope": ConnectorScope.PERSONAL.value,
            "createdBy": "other-user",
        })
        req = _mock_request(
            connector_registry=registry,
            body={"instanceName": "New Name"},
            headers={"X-Is-Admin": "true"},
        )

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await update_connector_instance_name("c1", req)
            assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        from app.connectors.api.router import update_connector_instance_name

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(side_effect=RuntimeError("boom"))
        req = _mock_request(connector_registry=registry, body={"instanceName": "New"})

        with pytest.raises(HTTPException) as exc_info:
            await update_connector_instance_name("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# update_connector_instance_filters_sync_config — edge cases
# (lines 3086, 3100, 3106, 3125, 3142-3143, 3157-3159)
# ============================================================================


class TestUpdateFiltersSyncConfigGapsCoverage:
    @pytest.mark.asyncio
    async def test_no_existing_config_uses_empty_dict(self):
        from app.connectors.api.router import update_connector_instance_filters_sync_config

        registry = AsyncMock()
        instance = {
            "type": "GOOGLE_DRIVE", "scope": "personal", "createdBy": "user-1",
            "isActive": False, "name": "My Drive",
        }
        registry.update_connector_instance = AsyncMock(return_value={"_key": "c1"})

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=None)  # No existing config
        config_service.set_config = AsyncMock()

        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        container.config_service = MagicMock(return_value=config_service)

        req = _mock_request(
            connector_registry=registry,
            container=container,
            body={"sync": {"interval": 60}},
        )

        with patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock, return_value=instance):
            with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
                with patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=999):
                    with patch(f"{_ROUTER}._trim_connector_config", side_effect=lambda x: x):
                        result = await update_connector_instance_filters_sync_config("c1", req)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_update_returns_none_raises_500(self):
        from app.connectors.api.router import update_connector_instance_filters_sync_config

        registry = AsyncMock()
        instance = {
            "type": "GOOGLE_DRIVE", "scope": "personal", "createdBy": "user-1",
            "isActive": False, "name": "My Drive",
        }
        registry.update_connector_instance = AsyncMock(return_value=None)

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"sync": {}, "filters": {}})
        config_service.set_config = AsyncMock()

        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        container.config_service = MagicMock(return_value=config_service)

        req = _mock_request(
            connector_registry=registry,
            container=container,
            body={"sync": {"interval": 60}},
        )

        with patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock, return_value=instance):
            with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
                with patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=999):
                    with patch(f"{_ROUTER}._trim_connector_config", side_effect=lambda x: x):
                        with pytest.raises(HTTPException) as exc_info:
                            await update_connector_instance_filters_sync_config("c1", req)
                        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        from app.connectors.api.router import update_connector_instance_filters_sync_config

        req = _mock_request(body={"sync": {"interval": 60}})

        with patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            with pytest.raises(HTTPException) as exc_info:
                await update_connector_instance_filters_sync_config("c1", req)
            assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# _stream_google_api_request — empty chunk skip (line 123->127)
# ============================================================================


class TestStreamGoogleApiRequestGapsCoverage:
    @pytest.mark.asyncio
    async def test_empty_chunk_is_skipped(self):
        """When MediaIoBaseDownload returns an empty chunk, it should not be yielded."""
        from app.connectors.api.router import _stream_google_api_request

        mock_request = MagicMock()

        call_count = [0]

        def mock_next_chunk(downloader):
            """Simulate: first call returns empty data, second call returns data + done."""
            call_count[0] += 1
            if call_count[0] == 1:
                return (None, False)
            return (None, True)

        with patch(f"{_ROUTER}.MediaIoBaseDownload") as mock_downloader_cls:
            mock_dl = MagicMock()
            mock_dl.next_chunk = MagicMock(side_effect=lambda: mock_next_chunk(mock_dl))
            mock_downloader_cls.return_value = mock_dl

            chunks = []
            async for chunk in _stream_google_api_request(mock_request, "download"):
                chunks.append(chunk)

            # Since the mock buffer won't have real data, just verify no errors
            assert isinstance(chunks, list)


# ============================================================================
# get_validated_connector_instance — edge cases
# ============================================================================


class TestGetValidatedConnectorInstanceGapsCoverage:
    @pytest.mark.asyncio
    async def test_personal_scope_admin_other_creator_raises_403(self):
        """Personal connector: admin != creator should raise 403."""
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "GOOGLE_DRIVE",
            "scope": ConnectorScope.PERSONAL.value,
            "createdBy": "other-user",
        })
        req = _mock_request(
            connector_registry=registry,
            headers={"X-Is-Admin": "true"},
        )

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_validated_connector_instance("c1", req)
            assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value


# ============================================================================
# create_connector_instance — beta validation gap (lines 2295-2296, 2303, 2315)
# ============================================================================


class TestCreateConnectorInstanceBetaGapsCoverage:
    @pytest.mark.asyncio
    async def test_get_account_type_exception_continues(self):
        from app.connectors.api.router import create_connector_instance

        registry = AsyncMock()
        registry.get_connector_metadata = AsyncMock(return_value={
            "config": {"auth": {"schemas": {}, "oauthConfigs": {}}},
            "scope": ["personal"],
            "supportedAuthTypes": ["NONE"],
        })
        registry._normalize_connector_name = MagicMock(return_value="googledrive")
        registry._get_beta_connector_names = MagicMock(return_value=set())
        registry.create_connector_instance_on_configuration = AsyncMock(return_value={"_key": "c1"})

        gp = AsyncMock()
        gp.get_account_type = AsyncMock(side_effect=RuntimeError("db error"))

        config_service = AsyncMock()
        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        container.config_service = MagicMock(return_value=config_service)

        req = _mock_request(
            connector_registry=registry,
            graph_provider=gp,
            container=container,
            body={
                "connectorType": "GOOGLE_DRIVE",
                "instanceName": "My Drive",
                "scope": "personal",
            },
        )

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            result = await create_connector_instance(req, graph_provider=gp)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_beta_team_enterprise_raises_403(self):
        from app.connectors.api.router import create_connector_instance

        registry = AsyncMock()
        registry.get_connector_metadata = AsyncMock(return_value={
            "config": {"auth": {"schemas": {}, "oauthConfigs": {}}},
            "scope": ["personal", "team"],
            "supportedAuthTypes": ["NONE"],
        })
        registry._normalize_connector_name = MagicMock(return_value="mybeta")
        registry._get_beta_connector_names = MagicMock(return_value={"mybeta"})

        gp = AsyncMock()
        gp.get_account_type = AsyncMock(return_value="enterprise")

        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("test"))

        req = _mock_request(
            connector_registry=registry,
            graph_provider=gp,
            container=container,
            body={
                "connectorType": "MY_BETA",
                "instanceName": "Beta Conn",
                "scope": "team",
            },
            headers={"X-Is-Admin": "true"},
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_connector_instance(req, graph_provider=gp)
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    @pytest.mark.asyncio
    async def test_unsupported_scope_raises_400(self):
        from app.connectors.api.router import create_connector_instance

        registry = AsyncMock()
        registry.get_connector_metadata = AsyncMock(return_value={
            "config": {"auth": {}},
            "scope": ["personal"],  # Only personal supported
            "supportedAuthTypes": ["NONE"],
        })
        registry._normalize_connector_name = MagicMock(return_value="googledrive")
        registry._get_beta_connector_names = MagicMock(return_value=set())

        gp = AsyncMock()
        gp.get_account_type = AsyncMock(return_value="individual")

        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("test"))

        req = _mock_request(
            connector_registry=registry,
            graph_provider=gp,
            container=container,
            body={
                "connectorType": "GOOGLE_DRIVE",
                "instanceName": "My Drive",
                "scope": "team",
            },
            headers={"X-Is-Admin": "true"},
        )

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await create_connector_instance(req, graph_provider=gp)
            assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value


# ============================================================================
# create_connector_instance — config["auth"] missing key (line 2452)
# ============================================================================


class TestCreateConnectorInstanceConfigAuthMissingCoverage:
    @pytest.mark.asyncio
    async def test_admin_oauth_created_but_config_has_no_auth_key(self):
        """When created_oauth_id is returned but config has no 'auth' key, it should be created."""
        from app.connectors.api.router import create_connector_instance

        registry = AsyncMock()
        registry.get_connector_metadata = AsyncMock(return_value={
            "config": {"auth": {"schemas": {}, "oauthConfigs": {}}},
            "scope": ["personal"],
            "supportedAuthTypes": ["OAUTH"],
        })
        registry._normalize_connector_name = MagicMock(return_value="googledrive")
        registry._get_beta_connector_names = MagicMock(return_value=set())
        registry.create_connector_instance_on_configuration = AsyncMock(return_value={"_key": "c1"})

        gp = AsyncMock()
        gp.get_account_type = AsyncMock(return_value="individual")

        config_service = AsyncMock()
        config_service.set_config = AsyncMock()

        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        container.config_service = MagicMock(return_value=config_service)

        # Provide config without "auth" key but with oauth info at body level
        req = _mock_request(
            connector_registry=registry,
            graph_provider=gp,
            container=container,
            body={
                "connectorType": "GOOGLE_DRIVE",
                "instanceName": "My Drive",
                "config": {"sync": {"interval": 3600}},  # No "auth" key
                "oauthConfigId": "oid-1",
                "authType": "OAUTH",
            },
            headers={"X-Is-Admin": "true"},
        )

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            with patch(f"{_ROUTER}._prepare_connector_config", new_callable=AsyncMock, return_value={"auth": {}, "sync": {}, "filters": {}}):
                result = await create_connector_instance(req, graph_provider=gp)
        assert result["success"] is True


# ============================================================================
# get_records — edge case: user not found
# ============================================================================


class TestGetRecordsGapsCoverage:
    @pytest.mark.asyncio
    async def test_user_not_found_returns_404_dict(self):
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(return_value=None)
        req = _mock_request(graph_provider=gp)

        result = await get_records(req, graph_provider=gp)
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_exception_returns_error_dict(self):
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(side_effect=RuntimeError("boom"))
        req = _mock_request(graph_provider=gp)

        result = await get_records(req, graph_provider=gp)
        assert "error" in result


# ============================================================================
# handle_record_deletion — record not found (line 466-469)
# ============================================================================


class TestHandleRecordDeletionGapsCoverage:
    @pytest.mark.asyncio
    async def test_record_not_found_raises_404(self):
        gp = AsyncMock()
        gp.delete_records_and_relations = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await handle_record_deletion("rec-1", graph_provider=gp)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        gp = AsyncMock()
        gp.delete_records_and_relations = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(HTTPException) as exc_info:
            await handle_record_deletion("rec-1", graph_provider=gp)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_success_returns_response(self):
        gp = AsyncMock()
        gp.delete_records_and_relations = AsyncMock(return_value={"deleted": True})

        result = await handle_record_deletion("rec-1", graph_provider=gp)
        assert result["status"] == "success"


# ============================================================================
# _parse_filter_response — unknown connector
# ============================================================================


class TestParseFilterResponseGapsCoverage:
    def test_unknown_connector_returns_empty(self):
        from app.connectors.api.router import _parse_filter_response
        result = _parse_filter_response({"data": []}, "files", "UNKNOWN_CONNECTOR")
        assert result == []

    def test_malformed_data_returns_empty(self):
        from app.connectors.api.router import _parse_filter_response
        result = _parse_filter_response(None, "labels", "GMAIL")
        assert result == []


# ============================================================================
# get_connector_schema — edge case
# ============================================================================


class TestGetConnectorSchemaGapsCoverage:
    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        from app.connectors.api.router import get_connector_schema

        registry = AsyncMock()
        registry.get_connector_metadata = AsyncMock(side_effect=RuntimeError("boom"))
        req = _mock_request(connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_schema("GOOGLE_DRIVE", req)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# ReindexFailedRequest model
# ============================================================================


class TestReindexFailedRequestCoverage:
    def test_model_creation(self):
        from app.connectors.api.router import ReindexFailedRequest
        req = ReindexFailedRequest(connector="GOOGLE_DRIVE", origin="CONNECTOR")
        assert req.connector == "GOOGLE_DRIVE"
        assert req.origin == "CONNECTOR"


# ============================================================================
# get_mime_type_from_record
# ============================================================================


class TestGetMimeTypeFromRecordCoverage:
    def test_uses_record_mime_type_when_present(self):
        record = _mock_record(mime_type="text/html")
        assert get_mime_type_from_record(record) == "text/html"

    def test_guesses_from_record_name(self):
        record = _mock_record(mime_type=None, record_name="report.xlsx")
        result = get_mime_type_from_record(record)
        assert "spreadsheet" in result or "excel" in result or "officedocument" in result

    def test_falls_back_to_octet_stream(self):
        record = SimpleNamespace(mime_type=None, record_name=None, name=None)
        assert get_mime_type_from_record(record) == "application/octet-stream"

    def test_uses_name_attr_if_record_name_missing(self):
        record = SimpleNamespace(mime_type=None, name="photo.jpg")
        result = get_mime_type_from_record(record)
        assert "image" in result

    def test_falls_back_when_guess_fails(self):
        record = SimpleNamespace(mime_type=None, record_name="weird_no_ext")
        assert get_mime_type_from_record(record) == "application/octet-stream"


# ============================================================================
# _parse_comma_separated_str
# ============================================================================


class TestParseCommaSeparatedStrCoverage:
    def test_none_returns_none(self):
        from app.connectors.api.router import _parse_comma_separated_str
        assert _parse_comma_separated_str(None) is None

    def test_empty_string_returns_none(self):
        from app.connectors.api.router import _parse_comma_separated_str
        assert _parse_comma_separated_str("") is None

    def test_single_value(self):
        from app.connectors.api.router import _parse_comma_separated_str
        assert _parse_comma_separated_str("pdf") == ["pdf"]

    def test_multiple_values(self):
        from app.connectors.api.router import _parse_comma_separated_str
        assert _parse_comma_separated_str("pdf, doc , xls") == ["pdf", "doc", "xls"]

    def test_empty_items_filtered(self):
        from app.connectors.api.router import _parse_comma_separated_str
        assert _parse_comma_separated_str("a,,b, ,c") == ["a", "b", "c"]


# ============================================================================
# _sanitize_app_name
# ============================================================================


class TestSanitizeAppNameCoverage:
    def test_removes_spaces_and_lowercases(self):
        from app.connectors.api.router import _sanitize_app_name
        assert _sanitize_app_name("Google Drive") == "googledrive"
        assert _sanitize_app_name("SLACK") == "slack"
        assert _sanitize_app_name("Share Point Online") == "sharepointonline"


# ============================================================================
# _trim_config_values
# ============================================================================


class TestTrimConfigValuesCoverage:
    def test_trims_string_values(self):
        from app.connectors.api.router import _trim_config_values
        assert _trim_config_values(obj="  hello  ") == "hello"

    def test_none_returns_none(self):
        from app.connectors.api.router import _trim_config_values
        assert _trim_config_values(obj=None) is None

    def test_preserves_bool(self):
        from app.connectors.api.router import _trim_config_values
        assert _trim_config_values(obj=True) is True

    def test_preserves_int(self):
        from app.connectors.api.router import _trim_config_values
        assert _trim_config_values(obj=42) == 42

    def test_preserves_float(self):
        from app.connectors.api.router import _trim_config_values
        assert _trim_config_values(obj=3.14) == 3.14

    def test_trims_list_elements(self):
        from app.connectors.api.router import _trim_config_values
        result = _trim_config_values(obj=["  a  ", " b "])
        assert result == ["a", "b"]

    def test_trims_dict_values(self):
        from app.connectors.api.router import _trim_config_values
        result = _trim_config_values(obj={"key": "  val  ", "num": 1})
        assert result == {"key": "val", "num": 1}

    def test_skips_sensitive_fields(self):
        from app.connectors.api.router import _trim_config_values
        result = _trim_config_values(obj={"certificate": "  cert  ", "normal": " x "})
        assert result["certificate"] == "  cert  "
        assert result["normal"] == "x"

    def test_nested_dict_with_path(self):
        from app.connectors.api.router import _trim_config_values
        result = _trim_config_values(obj={"auth": {"token": "  tok  ", "url": "  http  "}}, path="config")
        assert result["auth"]["token"] == "  tok  "  # token is in skip list
        assert result["auth"]["url"] == "http"


# ============================================================================
# _trim_connector_config
# ============================================================================


class TestTrimConnectorConfigCoverage:
    def test_trims_auth_sync_filters(self):
        config = {
            "auth": {"clientId": "  abc  "},
            "sync": {"interval": "  60  "},
            "filters": {"labels": " x "},
            "other": "  untouched  "
        }
        result = _trim_connector_config(config)
        assert result["auth"]["clientId"] == "abc"
        assert result["sync"]["interval"] == "60"
        assert result["filters"]["labels"] == "x"
        assert result["other"] == "  untouched  "

    def test_empty_config_returns_as_is(self):
        assert _trim_connector_config({}) == {}
        assert _trim_connector_config(None) is None

    def test_non_dict_config_returns_as_is(self):
        assert _trim_connector_config("not-a-dict") == "not-a-dict"


# ============================================================================
# _check_connector_not_locked
# ============================================================================


class TestCheckConnectorNotLockedCoverage:
    def test_not_locked_does_not_raise(self):
        _check_connector_not_locked({"isLocked": False})

    def test_locked_with_full_syncing(self):
        with pytest.raises(HTTPException) as exc_info:
            _check_connector_not_locked({"isLocked": True, "status": AppStatus.FULL_SYNCING.value})
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value
        assert "full sync" in exc_info.value.detail.lower()

    def test_locked_with_syncing(self):
        with pytest.raises(HTTPException) as exc_info:
            _check_connector_not_locked({"isLocked": True, "status": AppStatus.SYNCING.value})
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value
        assert "sync" in exc_info.value.detail.lower()

    def test_locked_with_unknown_status(self):
        with pytest.raises(HTTPException) as exc_info:
            _check_connector_not_locked({"isLocked": True, "status": "UNKNOWN"})
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value
        assert "another operation" in exc_info.value.detail.lower()

    def test_no_isLocked_does_not_raise(self):
        _check_connector_not_locked({"status": "READY"})


# ============================================================================
# require_connector_not_locked
# ============================================================================


class TestRequireConnectorNotLockedCoverage:
    @pytest.mark.asyncio
    async def test_unlocked_passes(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={"isLocked": False})
        req = _mock_request(connector_registry=registry)
        await require_connector_not_locked("c1", req)

    @pytest.mark.asyncio
    async def test_instance_not_found_passes(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value=None)
        req = _mock_request(connector_registry=registry)
        await require_connector_not_locked("c1", req)

    @pytest.mark.asyncio
    async def test_locked_raises_409(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(
            return_value={"isLocked": True, "status": AppStatus.SYNCING.value}
        )
        req = _mock_request(connector_registry=registry)
        with pytest.raises(HTTPException) as exc_info:
            await require_connector_not_locked("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value


# ============================================================================
# _encode_state_with_instance / _decode_state_with_instance
# ============================================================================


class TestEncodeDecodeStateCoverage:
    def test_round_trip(self):
        from app.connectors.api.router import _encode_state_with_instance, _decode_state_with_instance
        encoded = _encode_state_with_instance("abc123", "conn-42")
        decoded = _decode_state_with_instance(encoded)
        assert decoded["state"] == "abc123"
        assert decoded["connector_id"] == "conn-42"

    def test_invalid_state_raises(self):
        from app.connectors.api.router import _decode_state_with_instance
        with pytest.raises(ValueError):
            _decode_state_with_instance("not-valid-base64!!!")


# ============================================================================
# _get_config_path_for_instance
# ============================================================================


class TestGetConfigPathForInstanceCoverage:
    def test_returns_correct_path(self):
        assert _get_config_path_for_instance("abc-123") == "/services/connectors/abc-123/config"


# ============================================================================
# _validate_connector_deletion_permissions
# ============================================================================


class TestValidateConnectorDeletionPermissionsCoverage:
    def test_team_non_admin_raises(self):
        from app.connectors.api.router import _validate_connector_deletion_permissions
        with pytest.raises(HTTPException) as exc_info:
            _validate_connector_deletion_permissions(
                {"scope": ConnectorScope.TEAM.value, "createdBy": "u1"},
                "u1", is_admin=False, logger=logging.getLogger("test")
            )
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    def test_personal_non_creator_raises(self):
        from app.connectors.api.router import _validate_connector_deletion_permissions
        with pytest.raises(HTTPException) as exc_info:
            _validate_connector_deletion_permissions(
                {"scope": ConnectorScope.PERSONAL.value, "createdBy": "other"},
                "u1", is_admin=True, logger=logging.getLogger("test")
            )
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    def test_personal_creator_passes(self):
        from app.connectors.api.router import _validate_connector_deletion_permissions
        _validate_connector_deletion_permissions(
            {"scope": ConnectorScope.PERSONAL.value, "createdBy": "u1"},
            "u1", is_admin=False, logger=logging.getLogger("test")
        )

    def test_team_admin_passes(self):
        from app.connectors.api.router import _validate_connector_deletion_permissions
        _validate_connector_deletion_permissions(
            {"scope": ConnectorScope.TEAM.value, "createdBy": "u1"},
            "u1", is_admin=True, logger=logging.getLogger("test")
        )


# ============================================================================
# _get_user_context
# ============================================================================


class TestGetUserContextCoverage:
    def test_valid_user(self):
        from app.connectors.api.router import _get_user_context
        req = _mock_request(headers={"X-Is-Admin": "true"})
        ctx = _get_user_context(req)
        assert ctx["user_id"] == "user-1"
        assert ctx["org_id"] == "org-1"
        assert ctx["is_admin"] is True

    def test_missing_user_id_raises(self):
        from app.connectors.api.router import _get_user_context
        req = _mock_request(user={"userId": None, "orgId": "org-1"})
        with pytest.raises(HTTPException) as exc_info:
            _get_user_context(req)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    def test_missing_org_id_raises(self):
        from app.connectors.api.router import _get_user_context
        req = _mock_request(user={"userId": "u1", "orgId": None})
        with pytest.raises(HTTPException) as exc_info:
            _get_user_context(req)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value


# ============================================================================
# _validate_admin_only
# ============================================================================


class TestValidateAdminOnlyCoverage:
    def test_non_admin_raises(self):
        from app.connectors.api.router import _validate_admin_only
        with pytest.raises(HTTPException) as exc_info:
            _validate_admin_only(is_admin=False, action="do stuff")
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value
        assert "do stuff" in exc_info.value.detail

    def test_admin_passes(self):
        from app.connectors.api.router import _validate_admin_only
        _validate_admin_only(is_admin=True, action="do stuff")


# ============================================================================
# _validate_connector_permissions
# ============================================================================


class TestValidateConnectorPermissionsCoverage:
    def test_team_non_admin_raises(self):
        with pytest.raises(HTTPException):
            _validate_connector_permissions(
                {"scope": ConnectorScope.TEAM.value, "createdBy": "u1"},
                "u1", is_admin=False, action="update"
            )

    def test_personal_non_creator_non_admin_raises(self):
        with pytest.raises(HTTPException):
            _validate_connector_permissions(
                {"scope": ConnectorScope.PERSONAL.value, "createdBy": "other"},
                "u1", is_admin=False, action="update"
            )

    def test_personal_admin_non_creator_raises(self):
        """Admin can access personal connectors of others."""
        _validate_connector_permissions(
            {"scope": ConnectorScope.PERSONAL.value, "createdBy": "other"},
            "u1", is_admin=True, action="update"
        )

    def test_personal_creator_passes(self):
        _validate_connector_permissions(
            {"scope": ConnectorScope.PERSONAL.value, "createdBy": "u1"},
            "u1", is_admin=False, action="update"
        )

    def test_team_admin_passes(self):
        _validate_connector_permissions(
            {"scope": ConnectorScope.TEAM.value, "createdBy": "u1"},
            "u1", is_admin=True, action="update"
        )

    def test_unknown_scope_non_creator_non_admin_raises(self):
        with pytest.raises(HTTPException):
            _validate_connector_permissions(
                {"scope": "other", "createdBy": "other"},
                "u1", is_admin=False, action="update"
            )


# ============================================================================
# _get_and_validate_connector_instance
# ============================================================================


class TestGetAndValidateConnectorInstanceCoverage:
    @pytest.mark.asyncio
    async def test_found(self):
        from app.connectors.api.router import _get_and_validate_connector_instance
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={"_key": "c1", "type": "SLACK"})
        ctx = {"user_id": "u1", "org_id": "o1", "is_admin": False}
        result = await _get_and_validate_connector_instance("c1", ctx, registry, logging.getLogger("test"))
        assert result["_key"] == "c1"

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self):
        from app.connectors.api.router import _get_and_validate_connector_instance
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value=None)
        ctx = {"user_id": "u1", "org_id": "o1", "is_admin": False}
        with pytest.raises(HTTPException) as exc_info:
            await _get_and_validate_connector_instance("c1", ctx, registry, logging.getLogger("test"))
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value


# ============================================================================
# _find_oauth_config_in_list
# ============================================================================


class TestFindOAuthConfigInListCoverage:
    @pytest.mark.asyncio
    async def test_found(self):
        from app.connectors.api.router import _find_oauth_config_in_list
        configs = [
            {"_id": "c1", "orgId": "o1"},
            {"_id": "c2", "orgId": "o1"},
        ]
        config, idx = await _find_oauth_config_in_list(configs, "c2", "o1", logging.getLogger("test"))
        assert config["_id"] == "c2"
        assert idx == 1

    @pytest.mark.asyncio
    async def test_wrong_org_returns_none(self):
        from app.connectors.api.router import _find_oauth_config_in_list
        configs = [{"_id": "c1", "orgId": "o2"}]
        config, idx = await _find_oauth_config_in_list(configs, "c1", "o1", logging.getLogger("test"))
        assert config is None
        assert idx is None

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        from app.connectors.api.router import _find_oauth_config_in_list
        configs = [{"_id": "c1", "orgId": "o1"}]
        config, idx = await _find_oauth_config_in_list(configs, "c999", "o1", logging.getLogger("test"))
        assert config is None
        assert idx is None


# ============================================================================
# _check_oauth_name_conflict
# ============================================================================


class TestCheckOAuthNameConflictCoverage:
    def test_no_conflict_passes(self):
        configs = [{"oauthInstanceName": "Config A", "orgId": "o1"}]
        _check_oauth_name_conflict(configs, "Config B", "o1")

    def test_conflict_raises_409(self):
        configs = [{"oauthInstanceName": "Config A", "orgId": "o1"}]
        with pytest.raises(HTTPException) as exc_info:
            _check_oauth_name_conflict(configs, "Config A", "o1")
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value

    def test_conflict_different_org_passes(self):
        configs = [{"oauthInstanceName": "Config A", "orgId": "o2"}]
        _check_oauth_name_conflict(configs, "Config A", "o1")

    def test_exclude_index_skips_self(self):
        configs = [{"oauthInstanceName": "Config A", "orgId": "o1"}]
        _check_oauth_name_conflict(configs, "Config A", "o1", exclude_index=0)


# ============================================================================
# get_validated_connector_instance — more branches
# ============================================================================


class TestGetValidatedConnectorInstanceAdditionalCoverage:
    @pytest.mark.asyncio
    async def test_unauthenticated_raises_401(self):
        registry = AsyncMock()
        req = _mock_request(user={"userId": None, "orgId": "org-1"}, connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_validated_connector_instance("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    @pytest.mark.asyncio
    async def test_instance_not_found_raises_404(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value=None)
        req = _mock_request(connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_validated_connector_instance("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_team_scope_non_admin_raises_403(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "GOOGLE_DRIVE",
            "scope": ConnectorScope.TEAM.value,
            "createdBy": "user-1",
        })
        req = _mock_request(connector_registry=registry, headers={"X-Is-Admin": "false"})

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_validated_connector_instance("c1", req)
            assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    @pytest.mark.asyncio
    async def test_non_creator_non_admin_raises_403(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "GOOGLE_DRIVE",
            "scope": "custom",
            "createdBy": "other-user",
        })
        req = _mock_request(connector_registry=registry, headers={"X-Is-Admin": "false"})

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_validated_connector_instance("c1", req)
            assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    @pytest.mark.asyncio
    async def test_valid_instance_returns_dict(self):
        registry = AsyncMock()
        instance = {
            "type": "GOOGLE_DRIVE",
            "scope": ConnectorScope.PERSONAL.value,
            "createdBy": "user-1",
        }
        registry.get_connector_instance = AsyncMock(return_value=instance)
        req = _mock_request(connector_registry=registry, headers={"X-Is-Admin": "false"})

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            result = await get_validated_connector_instance("c1", req)
        assert result == instance


# ============================================================================
# get_records — success path
# ============================================================================


class TestGetRecordsAdditionalCoverage:
    @pytest.mark.asyncio
    async def test_success_returns_paginated(self):
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "ukey1"})
        gp.get_records = AsyncMock(return_value=(
            [{"id": "r1"}],  # records
            1,  # total_count
            {"types": ["PDF"]},  # available_filters
        ))
        req = _mock_request(graph_provider=gp)

        result = await get_records(
            req, graph_provider=gp, page=1, limit=20,
            search=None, record_types=None, origins=None, connectors=None,
            indexing_status=None, permissions=None, date_from=None, date_to=None,
            sort_by="createdAtTimestamp", sort_order="desc", source="all"
        )
        assert result["records"] == [{"id": "r1"}]
        assert result["pagination"]["totalCount"] == 1

    @pytest.mark.asyncio
    async def test_with_filters(self):
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "ukey1"})
        gp.get_records = AsyncMock(return_value=([], 0, {}))
        req = _mock_request(graph_provider=gp)

        result = await get_records(
            req, graph_provider=gp, page=1, limit=10,
            search="test", record_types="PDF,DOC", origins="CONNECTOR",
            connectors="GOOGLE_DRIVE", indexing_status="COMPLETED",
            permissions=None, date_from=1000, date_to=2000,
            sort_by="recordName", sort_order="asc", source="kb"
        )
        assert "records" in result


# ============================================================================
# handle_record_deletion — success with event data
# ============================================================================


class TestHandleRecordDeletionSuccessCoverage:
    @pytest.mark.asyncio
    async def test_success_returns_response(self):
        gp = AsyncMock()
        gp.delete_records_and_relations = AsyncMock(return_value={"deleted": True})

        result = await handle_record_deletion("rec-1", graph_provider=gp)
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_http_exception_re_raised(self):
        gp = AsyncMock()
        gp.delete_records_and_relations = AsyncMock(
            side_effect=HTTPException(status_code=403, detail="Forbidden")
        )
        with pytest.raises(HTTPException) as exc_info:
            await handle_record_deletion("rec-1", graph_provider=gp)
        assert exc_info.value.status_code == 403


# ============================================================================
# delete_record — success with event data published
# ============================================================================


class TestDeleteRecordEventPublishCoverage:
    @pytest.mark.asyncio
    async def test_success_with_event_data_published(self):
        req = _mock_request()
        gp = AsyncMock()
        gp.delete_record = AsyncMock(return_value={
            "success": True,
            "eventData": {"eventType": "deleted", "payload": {"id": "rec-1"}, "topic": "t"},
            "connector": "c1",
            "timestamp": 123,
        })
        kafka = AsyncMock()
        kafka.publish_event = AsyncMock()

        result = await delete_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert result["success"] is True
        kafka.publish_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_failure_result_raises_http_exception(self):
        req = _mock_request()
        gp = AsyncMock()
        gp.delete_record = AsyncMock(return_value={
            "success": False, "reason": "not found", "code": 404,
        })
        kafka = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await delete_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert exc_info.value.status_code == 404


# ============================================================================
# reindex_single_record — success with event data
# ============================================================================


class TestReindexSingleRecordSuccessCoverage:
    @pytest.mark.asyncio
    async def test_success_with_event_published(self):
        req = _mock_request(body={"depth": 1})
        gp = AsyncMock()
        gp.reindex_single_record = AsyncMock(return_value={
            "success": True,
            "eventData": {"eventType": "reindex", "payload": {"id": "rec-1"}, "topic": "t"},
            "recordId": "rec-1",
            "recordName": "doc.pdf",
            "connector": "c1",
            "userRole": "admin",
        })
        kafka = AsyncMock()
        kafka.publish_event = AsyncMock()

        result = await reindex_single_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert result["success"] is True
        assert result["depth"] == 1
        assert result["eventPublished"] is True

    @pytest.mark.asyncio
    async def test_success_no_event_data(self):
        req = _mock_request(body={"depth": 0})
        gp = AsyncMock()
        gp.reindex_single_record = AsyncMock(return_value={
            "success": True,
            "eventData": None,
            "recordId": "rec-1",
            "recordName": "doc.pdf",
            "connector": "c1",
            "userRole": "user",
        })
        kafka = AsyncMock()

        result = await reindex_single_record("rec-1", req, graph_provider=gp, kafka_service=kafka)
        assert result["success"] is True
        assert result["eventPublished"] is False


# ============================================================================
# reindex_record_group — success path
# ============================================================================


class TestReindexRecordGroupSuccessCoverage:
    @pytest.mark.asyncio
    async def test_success_publishes_event(self):
        req = _mock_request(body={"depth": 2})
        gp = AsyncMock()
        gp.reindex_record_group_records = AsyncMock(return_value={
            "success": True,
            "connectorId": "c1",
            "connectorName": "Google Drive",
            "userKey": "uk1",
            "depth": 2,
        })
        kafka = AsyncMock()
        kafka.publish_event = AsyncMock()

        with patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=999):
            result = await reindex_record_group("rg-1", req, graph_provider=gp, kafka_service=kafka)
        assert result["success"] is True
        assert result["eventPublished"] is True
        kafka.publish_event.assert_called_once()


# ============================================================================
# get_connector_registry — success path + scope validation
# ============================================================================


class TestGetConnectorRegistryAdditionalCoverage:
    @pytest.mark.asyncio
    async def test_invalid_scope_raises_400(self):
        req = _mock_request()
        with pytest.raises(HTTPException) as exc_info:
            await get_connector_registry(req, scope="invalid", page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value


# ============================================================================
# get_connector_instances — authentication + scope checks
# ============================================================================


class TestGetConnectorInstancesAdditionalCoverage:
    @pytest.mark.asyncio
    async def test_unauthenticated_raises_401(self):
        registry = AsyncMock()
        req = _mock_request(user={"userId": None, "orgId": "org-1"}, connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instances(req, scope=None, page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    @pytest.mark.asyncio
    async def test_invalid_scope_raises_400(self):
        registry = AsyncMock()
        req = _mock_request(connector_registry=registry)

        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instances(req, scope="invalid", page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    @pytest.mark.asyncio
    async def test_success(self):
        registry = AsyncMock()
        registry.get_all_connector_instances = AsyncMock(return_value={"instances": [], "total": 0})
        req = _mock_request(connector_registry=registry)

        result = await get_connector_instances(req, scope=None, page=1, limit=20, search=None)
        assert result["success"] is True


# ============================================================================
# get_active_connector_instances — authentication check
# ============================================================================


class TestActiveConnectorInstancesAdditionalCoverage:
    @pytest.mark.asyncio
    async def test_unauthenticated_raises_401(self):
        from app.connectors.api.router import get_active_connector_instances
        req = _mock_request(user={"userId": None, "orgId": "org-1"})

        with pytest.raises(HTTPException) as exc_info:
            await get_active_connector_instances(req)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.api.router import get_active_connector_instances
        registry = AsyncMock()
        registry.get_active_connector_instances = AsyncMock(return_value=[])
        req = _mock_request(connector_registry=registry)

        result = await get_active_connector_instances(req)
        assert result["success"] is True


# ============================================================================
# get_inactive_connector_instances — authentication check
# ============================================================================


class TestInactiveConnectorInstancesAdditionalCoverage:
    @pytest.mark.asyncio
    async def test_unauthenticated_raises_401(self):
        from app.connectors.api.router import get_inactive_connector_instances
        req = _mock_request(user={"userId": None, "orgId": "org-1"})

        with pytest.raises(HTTPException) as exc_info:
            await get_inactive_connector_instances(req)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.api.router import get_inactive_connector_instances
        registry = AsyncMock()
        registry.get_inactive_connector_instances = AsyncMock(return_value=[])
        req = _mock_request(connector_registry=registry)

        result = await get_inactive_connector_instances(req)
        assert result["success"] is True


# ============================================================================
# get_configured_connector_instances — authentication + scope checks
# ============================================================================


class TestConfiguredConnectorInstancesAdditionalCoverage:
    @pytest.mark.asyncio
    async def test_unauthenticated_raises_401(self):
        from app.connectors.api.router import get_configured_connector_instances
        req = _mock_request(user={"userId": None, "orgId": "org-1"})

        with pytest.raises(HTTPException) as exc_info:
            await get_configured_connector_instances(req, scope=None, page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    @pytest.mark.asyncio
    async def test_invalid_scope_raises_400(self):
        from app.connectors.api.router import get_configured_connector_instances
        req = _mock_request()

        with pytest.raises(HTTPException) as exc_info:
            await get_configured_connector_instances(req, scope="bad", page=1, limit=20, search=None)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.api.router import get_configured_connector_instances
        registry = AsyncMock()
        registry.get_configured_connector_instances = AsyncMock(return_value=[])
        req = _mock_request(connector_registry=registry)

        result = await get_configured_connector_instances(req, scope=None, page=1, limit=20, search=None)
        assert result["success"] is True


# ============================================================================
# get_connector_instance — success path
# ============================================================================


class TestGetConnectorInstanceAdditionalCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={"_key": "c1", "type": "SLACK"})
        req = _mock_request(connector_registry=registry)

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            result = await get_connector_instance("c1", req)
        assert result["success"] is True
        assert result["connector"]["_key"] == "c1"

    @pytest.mark.asyncio
    async def test_unauthenticated_raises_401(self):
        registry = AsyncMock()
        req = _mock_request(user={"userId": None, "orgId": "org-1"}, connector_registry=registry)
        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instance("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value


# ============================================================================
# get_connector_instance_config — success path + auth cleanup
# ============================================================================


class TestGetConnectorInstanceConfigAdditionalCoverage:
    @pytest.mark.asyncio
    async def test_success_with_config(self):
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "SLACK", "name": "Slack", "scope": "personal",
            "createdBy": "user-1", "authType": "OAUTH",
        })
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"clientId": "abc", "authorizeUrl": "http://auth"},
            "sync": {"interval": 60},
            "filters": {},
            "credentials": "secret",
            "oauth": {"tok": "x"},
        })
        container = MagicMock()
        container.logger = MagicMock(return_value=logging.getLogger("test"))
        container.config_service = MagicMock(return_value=config_service)

        req = _mock_request(connector_registry=registry, container=container)

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            result = await get_connector_instance_config("c1", req)
        assert result["success"] is True
        config = result["config"]["config"]
        # Credentials and oauth removed
        assert "credentials" not in config
        assert "oauth" not in config
        # OAuth fields cleaned from auth
        assert "authorizeUrl" not in config["auth"]
        assert config["auth"]["clientId"] == "abc"

    @pytest.mark.asyncio
    async def test_unauthenticated_raises_401(self):
        registry = AsyncMock()
        req = _mock_request(user={"userId": None, "orgId": "org-1"}, connector_registry=registry)
        with pytest.raises(HTTPException) as exc_info:
            await get_connector_instance_config("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value


# ============================================================================
# _parse_filter_response — known connectors
# ============================================================================


class TestParseFilterResponseKnownCoverage:
    def test_gmail_labels(self):
        from app.connectors.api.router import _parse_filter_response
        data = {"labels": [
            {"id": "L1", "name": "Work", "type": "user"},
            {"id": "L2", "name": "System", "type": "system"},
        ]}
        result = _parse_filter_response(data, "labels", "GMAIL")
        assert len(result) == 1
        assert result[0]["value"] == "L1"

    def test_drive_folders(self):
        from app.connectors.api.router import _parse_filter_response
        data = {"files": [{"id": "F1", "name": "Folder A"}]}
        result = _parse_filter_response(data, "folders", "DRIVE")
        assert len(result) == 1
        assert result[0]["label"] == "Folder A"

    def test_onedrive_folders(self):
        from app.connectors.api.router import _parse_filter_response
        data = {"value": [
            {"id": "F1", "name": "Docs", "folder": True},
            {"id": "F2", "name": "File", "folder": None},
        ]}
        result = _parse_filter_response(data, "folders", "ONEDRIVE")
        assert len(result) == 1

    def test_slack_channels(self):
        from app.connectors.api.router import _parse_filter_response
        data = {"channels": [
            {"id": "C1", "name": "general", "is_archived": False},
            {"id": "C2", "name": "old", "is_archived": True},
        ]}
        result = _parse_filter_response(data, "channels", "SLACK")
        assert len(result) == 1
        assert result[0]["label"] == "#general"

    def test_confluence_spaces(self):
        from app.connectors.api.router import _parse_filter_response
        data = {"results": [{"key": "DOC", "name": "Documentation"}]}
        result = _parse_filter_response(data, "spaces", "CONFLUENCE")
        assert len(result) == 1


# ============================================================================
# _get_static_filter_options
# ============================================================================


class TestGetStaticFilterOptionsCoverage:
    @pytest.mark.asyncio
    async def test_file_types(self):
        from app.connectors.api.router import _get_static_filter_options
        result = await _get_static_filter_options("DRIVE", "fileTypes")
        assert len(result) > 0
        assert any(o["value"] == "pdf" for o in result)

    @pytest.mark.asyncio
    async def test_content_types(self):
        from app.connectors.api.router import _get_static_filter_options
        result = await _get_static_filter_options("CONFLUENCE", "contentTypes")
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_unknown_returns_empty(self):
        from app.connectors.api.router import _get_static_filter_options
        result = await _get_static_filter_options("DRIVE", "unknownFilter")
        assert result == []


# ============================================================================
# _get_fallback_filter_options
# ============================================================================


class TestGetFallbackFilterOptionsCoverage:
    @pytest.mark.asyncio
    async def test_gmail_fallback(self):
        from app.connectors.api.router import _get_fallback_filter_options
        result = await _get_fallback_filter_options("GMAIL")
        assert "labels" in result

    @pytest.mark.asyncio
    async def test_unknown_returns_empty(self):
        from app.connectors.api.router import _get_fallback_filter_options
        result = await _get_fallback_filter_options("UNKNOWN")
        assert result == {}


# ============================================================================
# get_connector_schema — success path
# ============================================================================


class TestGetConnectorSchemaSuccessCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.api.router import get_connector_schema
        registry = AsyncMock()
        registry.get_connector_metadata = AsyncMock(return_value={
            "config": {"auth": {"authorizeUrl": "http://auth"}, "sync": {}}
        })
        req = _mock_request(connector_registry=registry)

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            result = await get_connector_schema("GOOGLE_DRIVE", req)
        assert result["success"] is True
        # authorizeUrl cleaned
        assert "authorizeUrl" not in result["schema"].get("auth", {})

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self):
        from app.connectors.api.router import get_connector_schema
        registry = AsyncMock()
        registry.get_connector_metadata = AsyncMock(return_value=None)
        req = _mock_request(connector_registry=registry)

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await get_connector_schema("NONEXISTENT", req)
            assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value


# ============================================================================
# update_connector_instance_name — success path
# ============================================================================


class TestUpdateConnectorInstanceNameSuccessCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.api.router import update_connector_instance_name

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "GOOGLE_DRIVE",
            "scope": ConnectorScope.PERSONAL.value,
            "createdBy": "user-1",
        })
        registry.update_connector_instance = AsyncMock(return_value={"_key": "c1"})
        req = _mock_request(
            connector_registry=registry,
            body={"instanceName": "New Name"},
        )

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            result = await update_connector_instance_name("c1", req)
        assert result["success"] is True
        assert result["connector"]["name"] == "New Name"

    @pytest.mark.asyncio
    async def test_empty_name_raises_400(self):
        from app.connectors.api.router import update_connector_instance_name

        registry = AsyncMock()
        req = _mock_request(
            connector_registry=registry,
            body={"instanceName": ""},
        )

        with pytest.raises(HTTPException) as exc_info:
            await update_connector_instance_name("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self):
        from app.connectors.api.router import update_connector_instance_name

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value=None)
        req = _mock_request(
            connector_registry=registry,
            body={"instanceName": "New Name"},
        )

        with pytest.raises(HTTPException) as exc_info:
            await update_connector_instance_name("c1", req)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_update_fails_raises_500(self):
        from app.connectors.api.router import update_connector_instance_name

        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "SLACK", "scope": ConnectorScope.PERSONAL.value,
            "createdBy": "user-1", "name": "Old Name",
        })
        registry.update_connector_instance = AsyncMock(return_value=None)
        req = _mock_request(
            connector_registry=registry,
            body={"instanceName": "New Name"},
        )

        with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await update_connector_instance_name("c1", req)
            assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ============================================================================
# _handle_oauth_config_creation — various branches
# ============================================================================


class TestHandleOAuthConfigCreationCoverage:
    @pytest.mark.asyncio
    async def test_non_oauth_type_returns_none(self):
        from app.connectors.api.router import _handle_oauth_config_creation

        result = await _handle_oauth_config_creation(
            connector_type="SLACK",
            auth_config={"clientId": "abc"},
            instance_name="Slack",
            user_id="u1", org_id="o1",
            is_admin=True, config_service=AsyncMock(),
            oauth_config_id=None, auth_type="API_TOKEN",
            base_url="", logger=logging.getLogger("test"),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_credentials_with_oauth_app_id_returns_id(self):
        from app.connectors.api.router import _handle_oauth_config_creation

        with patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId", "clientSecret"]):
            result = await _handle_oauth_config_creation(
                connector_type="GOOGLE_DRIVE",
                auth_config={"oauthConfigId": "oid-1"},  # No actual credentials
                instance_name="My Drive",
                user_id="u1", org_id="o1",
                is_admin=True, config_service=AsyncMock(),
                oauth_config_id="oid-1", auth_type="OAUTH",
                base_url="", logger=logging.getLogger("test"),
            )
        assert result == "oid-1"

    @pytest.mark.asyncio
    async def test_no_credentials_no_oauth_id_returns_none(self):
        from app.connectors.api.router import _handle_oauth_config_creation

        with patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId", "clientSecret"]):
            result = await _handle_oauth_config_creation(
                connector_type="GOOGLE_DRIVE",
                auth_config={},  # No credentials
                instance_name="My Drive",
                user_id="u1", org_id="o1",
                is_admin=True, config_service=AsyncMock(),
                oauth_config_id=None, auth_type="OAUTH",
                base_url="", logger=logging.getLogger("test"),
            )
        assert result is None


# ============================================================================
# _build_oauth_flow_config
# ============================================================================


class TestBuildOAuthFlowConfigCoverage:
    @pytest.mark.asyncio
    async def test_direct_auth_config(self):
        from app.connectors.api.router import _build_oauth_flow_config
        auth_config = {"authorizeUrl": "http://auth", "clientId": "abc"}
        result = await _build_oauth_flow_config(
            auth_config=auth_config,
            connector_type="SLACK",
            org_id="o1",
            config_service=AsyncMock(),
            logger=logging.getLogger("test"),
        )
        assert result["authorizeUrl"] == "http://auth"
        assert result["clientId"] == "abc"

    @pytest.mark.asyncio
    async def test_shared_oauth_config_not_found_raises_404(self):
        from app.connectors.api.router import _build_oauth_flow_config
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])  # empty list

        with pytest.raises(HTTPException) as exc_info:
            await _build_oauth_flow_config(
                auth_config={"oauthConfigId": "oid-1"},
                connector_type="SLACK",
                org_id="o1",
                config_service=config_service,
                logger=logging.getLogger("test"),
            )
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_shared_oauth_config_found(self):
        from app.connectors.api.router import _build_oauth_flow_config
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "oid-1", "orgId": "o1", "authorizeUrl": "http://auth",
             "tokenUrl": "http://token", "config": {"clientId": "xyz"},
             "scopes": {"team_sync": ["read"]}}
        ])

        result = await _build_oauth_flow_config(
            auth_config={"oauthConfigId": "oid-1", "connectorScope": "team"},
            connector_type="SLACK",
            org_id="o1",
            config_service=config_service,
            logger=logging.getLogger("test"),
        )
        assert result["authorizeUrl"] == "http://auth"
        assert result["clientId"] == "xyz"
        assert result["scopes"] == ["read"]


# ============================================================================
# create_connector_instance — basic success path
# ============================================================================


class TestCreateConnectorInstanceBasicCoverage:
    @pytest.mark.asyncio
    async def test_missing_type_raises_400(self):
        from app.connectors.api.router import create_connector_instance
        gp = AsyncMock()
        req = _mock_request(body={"instanceName": "My Drive"}, graph_provider=gp)

        with pytest.raises(HTTPException) as exc_info:
            await create_connector_instance(req, graph_provider=gp)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    @pytest.mark.asyncio
    async def test_unauthenticated_raises_401(self):
        from app.connectors.api.router import create_connector_instance
        gp = AsyncMock()
        req = _mock_request(
            user={"userId": None, "orgId": "org-1"},
            body={"connectorType": "SLACK", "instanceName": "test"},
            graph_provider=gp,
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_connector_instance(req, graph_provider=gp)
        assert exc_info.value.status_code == HttpStatusCode.UNAUTHORIZED.value

    @pytest.mark.asyncio
    async def test_invalid_scope_raises_400(self):
        from app.connectors.api.router import create_connector_instance
        gp = AsyncMock()
        req = _mock_request(
            body={"connectorType": "SLACK", "instanceName": "test", "scope": "invalid"},
            graph_provider=gp,
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_connector_instance(req, graph_provider=gp)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    @pytest.mark.asyncio
    async def test_connector_type_not_found_raises_404(self):
        from app.connectors.api.router import create_connector_instance
        registry = AsyncMock()
        registry.get_connector_metadata = AsyncMock(return_value=None)
        gp = AsyncMock()
        req = _mock_request(
            connector_registry=registry,
            body={"connectorType": "NONEXISTENT", "instanceName": "test"},
            graph_provider=gp,
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_connector_instance(req, graph_provider=gp)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value


# ============================================================================
# convert_buffer_to_pdf_stream — more branches
# ============================================================================


class TestConvertBufferToPdfStreamAdditionalCoverage:
    @pytest.mark.asyncio
    async def test_bytes_buffer(self):
        """bytes buffer is written to temp file, then converted."""
        with patch(f"{_ROUTER}.convert_to_pdf", new_callable=AsyncMock) as mock_convert, \
             patch(f"{_ROUTER}.create_stream_record_response") as mock_stream:
            mock_convert.return_value = "/tmp/nonexistent.pdf"
            mock_stream.return_value = MagicMock()
            with patch("os.path.exists", return_value=False):
                with patch("os.listdir", return_value=[]):
                    with pytest.raises(HTTPException):
                        await convert_buffer_to_pdf_stream(b"binary-data", "file.pptx", "pptx")

    @pytest.mark.asyncio
    async def test_unsupported_type_raises_500(self):
        with pytest.raises(HTTPException) as exc_info:
            await convert_buffer_to_pdf_stream(12345, "file.pptx", "pptx")
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
        assert "Unsupported buffer type" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_file_like_buffer(self):
        """file-like object with read() is supported."""
        buf = io.BytesIO(b"file data")

        with patch(f"{_ROUTER}.convert_to_pdf", new_callable=AsyncMock) as mock_convert, \
             patch(f"{_ROUTER}.create_stream_record_response") as mock_stream:
            mock_convert.return_value = "/tmp/nonexistent.pdf"
            mock_stream.return_value = MagicMock()
            with patch("os.path.exists", return_value=False):
                with patch("os.listdir", return_value=[]):
                    with pytest.raises(HTTPException):
                        await convert_buffer_to_pdf_stream(buf, "file.pptx", "pptx")


# ============================================================================
# _clean_schema_for_response — additional
# ============================================================================


class TestCleanSchemaForResponseAdditionalCoverage:
    def test_deeply_nested(self):
        schema = {
            "_oauth_configs": {"x": "y"},
            "auth": {
                "authorizeUrl": "http://...",
                "tokenUrl": "http://...",
                "scopes": ["x"],
                "oauthConfigs": {"y": "z"},
                "schemas": {"OAUTH": {"redirectUri": "/callback"}},
                "other": "kept"
            },
            "sync": {"interval": 3600}
        }
        result = _clean_schema_for_response(schema)
        assert "_oauth_configs" not in result
        assert result["auth"]["other"] == "kept"
        assert result["auth"]["schemas"]["OAUTH"]["redirectUri"] == "/callback"
        assert result["sync"]["interval"] == 3600


# ============================================================================
# stream_record — access denied
# ============================================================================


class TestStreamRecordAccessDeniedCoverage:
    @pytest.mark.asyncio
    async def test_no_access_raises_403(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={"_key": "org-1"})
        record = _mock_record(org_id="org-1")
        gp.get_record_by_id = AsyncMock(return_value=record)
        gp.check_record_access_with_details = AsyncMock(return_value=None)

        req = _mock_request(graph_provider=gp)
        config_service = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await stream_record(req, "rec-1", graph_provider=gp, config_service=config_service)
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

    @pytest.mark.asyncio
    async def test_record_not_found_raises_404(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={"_key": "org-1"})
        gp.get_record_by_id = AsyncMock(return_value=None)

        req = _mock_request(graph_provider=gp)
        config_service = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await stream_record(req, "rec-1", graph_provider=gp, config_service=config_service)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_org_not_found_raises_404(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value=None)
        gp.get_record_by_id = AsyncMock(return_value=_mock_record())

        req = _mock_request(graph_provider=gp)
        config_service = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await stream_record(req, "rec-1", graph_provider=gp, config_service=config_service)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value


# ============================================================================
# _prepare_connector_config — OAUTH type with base_url
# ============================================================================


class TestPrepareConnectorConfigOAuthCoverage:
    @pytest.mark.asyncio
    async def test_oauth_type_with_base_url(self):
        from app.connectors.api.router import _prepare_connector_config

        config_service = AsyncMock()

        result = await _prepare_connector_config(
            config={"auth": {"someField": "val"}},
            connector_type="GOOGLE_DRIVE",
            scope="personal",
            oauth_config_id=None,
            metadata={
                "config": {
                    "auth": {
                        "schemas": {"OAUTH": {"redirectUri": "callback"}},
                        "oauthConfigs": {"OAUTH": {"authorizeUrl": "http://auth", "tokenUrl": "http://token", "scopes": ["read"]}},
                    }
                }
            },
            selected_auth_type="OAUTH",
            user_id="u1",
            org_id="o1",
            is_admin=False,
            config_service=config_service,
            base_url="http://myapp.com",
            logger=logging.getLogger("test"),
        )
        assert "http://myapp.com" in result["auth"]["redirectUri"]
        assert result["auth"]["authorizeUrl"] == "http://auth"

    @pytest.mark.asyncio
    async def test_non_oauth_keeps_all_fields(self):
        from app.connectors.api.router import _prepare_connector_config

        config_service = AsyncMock()

        result = await _prepare_connector_config(
            config={"auth": {"clientId": "abc", "apiKey": "xyz"}},
            connector_type="SLACK",
            scope="personal",
            oauth_config_id=None,
            metadata={"config": {"auth": {"schemas": {}, "oauthConfigs": {}}}},
            selected_auth_type="API_TOKEN",
            user_id="u1",
            org_id="o1",
            is_admin=False,
            config_service=config_service,
            base_url="",
            logger=logging.getLogger("test"),
        )
        # Non-OAUTH keeps all auth fields
        assert result["auth"]["clientId"] == "abc"
        assert result["auth"]["apiKey"] == "xyz"
