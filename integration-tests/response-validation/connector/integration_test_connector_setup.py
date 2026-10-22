"""Response-validation ITs for connecting a source and starting its sync, for
connectors that need no OAuth sign-in.

- POST /api/v1/connectors
- GET  /api/v1/connectors/{connectorId}
- GET  /api/v1/connectors/{connectorId}/config
- PUT  /api/v1/connectors/{connectorId}/config/auth
- PUT  /api/v1/connectors/{connectorId}/config/filters-sync
- GET  /api/v1/connectors/{connectorId}/filters/{filterKey}/options
- POST /api/v1/connectors/{connectorId}/toggle

Request bodies are the ones the web app sends and the server accepts; each must
be expressible with the documented request schema. Each success response must
be fully described by pipeshub-openapi.yaml.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.clients.connectors_client import ConnectorsClient  # noqa: E402
from openapi_schema_validator import (  # noqa: E402
    _operation_responses,
    assert_error_response_matches_openapi_operation,
    assert_query_params_documented,
    assert_request_body_strictly_matches_openapi_operation,
    assert_response_strictly_matches_openapi_operation,
    request_body_violations,
)

_CREATE = "createConnectorInstance"
_GET = "getConnectorInstance"
_GET_CONFIG = "getConnectorConfig"
_UPDATE_AUTH = "updateConnectorAuthConfig"
_UPDATE_FILTERS_SYNC = "updateConnectorFiltersSyncConfig"
_FILTER_OPTIONS = "getFilterFieldOptions"
_TOGGLE = "toggleConnector"

SYNC_LOCK_TIMEOUT_SEC = 90


def _is_active(connectors: ConnectorsClient, connector_id: str) -> bool:
    resp = connectors.get_instance(connector_id)
    assert resp.status_code == 200, resp.text
    return bool(resp.json()["connector"].get("isActive"))


def _ensure_sync_off(connectors: ConnectorsClient, connector_id: str) -> None:
    if not _is_active(connectors, connector_id):
        return
    # A running full sync locks the connector (409) until it finishes.
    deadline = time.monotonic() + SYNC_LOCK_TIMEOUT_SEC
    resp = connectors.toggle(connector_id)
    while resp.status_code == 409 and time.monotonic() + 2 < deadline:
        time.sleep(2)
        resp = connectors.toggle(connector_id)
    assert resp.status_code == 200, f"could not turn sync back off: {resp.text}"
    # Toggle flips, so a 200 alone does not prove sync ended up off.
    assert not _is_active(connectors, connector_id), "sync is still on after turning it off"


@pytest.mark.integration
class TestCreateConnector:
    def test_create_request_body_matches_spec(self, rss_connector: Any) -> None:
        """RSS takes its feed URLs as sync settings in the create body."""
        assert_request_body_strictly_matches_openapi_operation(
            rss_connector.request_body, _CREATE
        )

    def test_create_with_credentials_request_body_matches_spec(
        self, postgres_connector: Any
    ) -> None:
        """Credentials go in ``config.auth`` as the fields the registry schema lists."""
        assert_request_body_strictly_matches_openapi_operation(
            postgres_connector.request_body, _CREATE
        )

    def test_create_response_matches_spec(self, rss_connector: Any) -> None:
        resp = rss_connector.response
        assert_response_strictly_matches_openapi_operation(
            resp.json(), _CREATE, status_code=str(resp.status_code)
        )


@pytest.mark.integration
class TestGetConnector:
    def test_get_instance_matches_spec(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        resp = connectors_api.get_instance(rss_connector.connector_id)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["connector"].get("isConfigured") is True, body

        assert_response_strictly_matches_openapi_operation(body, _GET)

    def test_list_configured_matches_spec(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        """The page of configured connectors is nested under ``connectors``."""
        resp = connectors_api.list_configured(scope="team", limit=200)
        assert resp.status_code == 200, resp.text
        page = resp.json()["connectors"]
        assert rss_connector.connector_id in {c["_key"] for c in page["connectors"]}
        assert_response_strictly_matches_openapi_operation(resp.json(), "listConfiguredConnectors")

    def test_get_unknown_instance_is_404(self, connectors_api: ConnectorsClient) -> None:
        resp = connectors_api.get_instance(str(uuid4()))
        assert resp.status_code == 404, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _GET, 404)

    def test_get_instance_with_markup_in_a_query_value_is_400(
        self, connectors_api: ConnectorsClient
    ) -> None:
        """The app-wide XSS filter answers before authentication, as the documented 400 says."""
        # The validator never reads descriptions, so the sentence is checked here.
        documented = _operation_responses(_GET)["400"]["description"]
        assert "HTML tags, scripts, and XSS content are not allowed" in documented, documented
        resp = connectors_api.get_instance(
            str(uuid4()), auth=False, params={"q": "<script>alert(1)</script>"}
        )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert_error_response_matches_openapi_operation(body, _GET, 400)
        assert body["error"]["code"] == "HTTP_BAD_REQUEST", body
        assert body["error"]["message"].startswith(
            'HTML tags, scripts, and XSS content are not allowed in the query parameter "q".'
        ), body

    def test_get_instance_with_a_malformed_id_is_400(
        self, connectors_api: ConnectorsClient
    ) -> None:
        """A connectorId outside ``^[A-Za-z0-9_-]{1,64}$`` is the documented VALIDATION_ERROR."""
        documented = _operation_responses(_GET)["400"]["description"]
        assert "`connectorId` is not 1 to 64 letters" in documented, documented
        resp = connectors_api.get_instance("x" * 65)
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert_error_response_matches_openapi_operation(body, _GET, 400)
        assert body["error"]["code"] == "VALIDATION_ERROR", body

    def test_get_instance_unauthenticated_is_401(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        resp = connectors_api.get_instance(rss_connector.connector_id, auth=False)
        assert resp.status_code == 401, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _GET, 401)


@pytest.mark.integration
class TestConnectorConfig:
    def test_get_config_matches_spec(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        resp = connectors_api.get_config(rss_connector.connector_id)
        assert resp.status_code == 200, resp.text
        assert_response_strictly_matches_openapi_operation(resp.json(), _GET_CONFIG)

    def test_get_config_with_credentials_matches_spec(
        self, connectors_api: ConnectorsClient, postgres_connector: Any
    ) -> None:
        resp = connectors_api.get_config(postgres_connector.connector_id)
        assert resp.status_code == 200, resp.text
        assert_response_strictly_matches_openapi_operation(resp.json(), _GET_CONFIG)

    def test_get_config_masks_saved_secrets(
        self, connectors_api: ConnectorsClient, postgres_connector: Any
    ) -> None:
        """A stored password never comes back, so reading the config is safe to expose."""
        sent_password = postgres_connector.request_body["config"]["auth"]["password"]
        resp = connectors_api.get_config(postgres_connector.connector_id)
        assert resp.status_code == 200, resp.text
        assert sent_password not in resp.text

    def test_update_auth_request_body_matches_spec(
        self, connectors_api: ConnectorsClient, postgres_connector: Any
    ) -> None:
        body = {"auth": {**postgres_connector.request_body["config"]["auth"], "port": 2}}
        resp = connectors_api.update_auth_config(postgres_connector.connector_id, body)
        assert resp.status_code == 200, resp.text

        assert_request_body_strictly_matches_openapi_operation(body, _UPDATE_AUTH)

    def test_update_auth_response_matches_spec(
        self, connectors_api: ConnectorsClient, postgres_connector: Any
    ) -> None:
        body = {"auth": {**postgres_connector.request_body["config"]["auth"], "port": 3}}
        resp = connectors_api.update_auth_config(postgres_connector.connector_id, body)
        assert resp.status_code == 200, resp.text

        assert_response_strictly_matches_openapi_operation(resp.json(), _UPDATE_AUTH)

    def test_update_filters_sync_request_body_matches_spec(
        self, connectors_api: ConnectorsClient, rss_connector: Any, rss_feed_url: str
    ) -> None:
        """The web app sends the connector's own sync settings beside the strategy."""
        body = {
            "sync": {
                "selectedStrategy": "MANUAL",
                "feed_urls": rss_feed_url,
                "max_articles_per_feed": 1,
            },
            "filters": {},
        }
        resp = connectors_api.update_filters_sync_config(rss_connector.connector_id, body)
        assert resp.status_code == 200, resp.text

        assert_request_body_strictly_matches_openapi_operation(body, _UPDATE_FILTERS_SYNC)

    def test_update_filters_sync_response_matches_spec(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        body = {"sync": {"selectedStrategy": "MANUAL"}, "filters": {}}
        resp = connectors_api.update_filters_sync_config(rss_connector.connector_id, body)
        assert resp.status_code == 200, resp.text

        assert_response_strictly_matches_openapi_operation(resp.json(), _UPDATE_FILTERS_SYNC)

    def test_update_filters_sync_scheduled_strategy(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        body = {
            "sync": {
                "selectedStrategy": "SCHEDULED",
                "scheduledConfig": {"intervalMinutes": 60, "timezone": "UTC"},
            },
            "filters": {},
        }
        try:
            resp = connectors_api.update_filters_sync_config(rss_connector.connector_id, body)
            assert resp.status_code == 200, resp.text
            assert_request_body_strictly_matches_openapi_operation(body, _UPDATE_FILTERS_SYNC)
        finally:
            connectors_api.update_filters_sync_config(
                rss_connector.connector_id, {"sync": {"selectedStrategy": "MANUAL"}}
            )

    def test_filter_options_unknown_filter_is_404(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        resp = connectors_api.get_filter_options(rss_connector.connector_id, "no_such_filter")
        assert resp.status_code == 404, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _FILTER_OPTIONS, 404)


@pytest.mark.integration
class TestDynamicFilterOptions:
    def test_filter_options_matches_spec(
        self, connectors_api: ConnectorsClient, minio_connector: Any
    ) -> None:
        params = {"page": 1, "limit": 20}
        assert_query_params_documented(_FILTER_OPTIONS, params)

        resp = connectors_api.get_filter_options(minio_connector.connector_id, "buckets", **params)
        assert resp.status_code == 200, resp.text
        assert resp.json().get("options"), f"MinIO listed no buckets: {resp.text}"

        assert_response_strictly_matches_openapi_operation(resp.json(), _FILTER_OPTIONS)


    def test_update_filters_sync_with_a_filter_value_matches_spec(
        self, connectors_api: ConnectorsClient, minio_connector: Any
    ) -> None:
        """A saved filter carries `type`: sync skips any entry without it."""
        value = {"operator": "in", "value": ["pipeshub-connector-test"], "type": "list"}
        body = {"filters": {"sync": {"values": {"buckets": value}}}}
        resp = connectors_api.update_filters_sync_config(minio_connector.connector_id, body)
        assert resp.status_code == 200, resp.text
        assert_request_body_strictly_matches_openapi_operation(body, _UPDATE_FILTERS_SYNC)

        saved = connectors_api.get_config(minio_connector.connector_id).json()
        assert saved["config"]["config"]["filters"]["sync"]["values"]["buckets"] == value

        untyped = {"filters": {"sync": {"values": {"buckets": {"operator": "in", "value": []}}}}}
        assert request_body_violations(untyped, _UPDATE_FILTERS_SYNC), (
            "the spec accepts a filter without `type`, which sync would skip"
        )


@pytest.mark.integration
class TestToggleSync:
    def test_toggle_sync_on_and_off_matches_spec(
        self,
        connectors_api: ConnectorsClient,
        rss_connector: Any,
        rss_feed_reachable: None,
    ) -> None:
        """Each call flips sync: the first starts the first sync, the second stops it."""
        connector_id = rss_connector.connector_id
        _ensure_sync_off(connectors_api, connector_id)
        body = {"type": "sync"}
        assert_request_body_strictly_matches_openapi_operation(body, _TOGGLE)
        try:
            on = connectors_api.toggle(connector_id, json=body)
            assert on.status_code == 200, on.text
            assert _is_active(connectors_api, connector_id)
            assert_response_strictly_matches_openapi_operation(on.json(), _TOGGLE)

            off = connectors_api.toggle(connector_id, json=body)
            assert off.status_code == 200, off.text
            assert not _is_active(connectors_api, connector_id)
            assert_response_strictly_matches_openapi_operation(off.json(), _TOGGLE)
        finally:
            _ensure_sync_off(connectors_api, connector_id)

    def test_toggle_full_sync_request_body_matches_spec(
        self,
        connectors_api: ConnectorsClient,
        rss_connector: Any,
        rss_feed_reachable: None,
    ) -> None:
        """``fullSync`` asks the first sync to start from scratch."""
        connector_id = rss_connector.connector_id
        _ensure_sync_off(connectors_api, connector_id)
        body = {"type": "sync", "fullSync": True}
        try:
            resp = connectors_api.toggle(connector_id, json=body)
            assert resp.status_code == 200, resp.text
            assert_request_body_strictly_matches_openapi_operation(body, _TOGGLE)
        finally:
            _ensure_sync_off(connectors_api, connector_id)

    def test_toggle_unknown_connector_is_404(self, connectors_api: ConnectorsClient) -> None:
        resp = connectors_api.toggle(str(uuid4()))
        assert resp.status_code == 404, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _TOGGLE, 404)
