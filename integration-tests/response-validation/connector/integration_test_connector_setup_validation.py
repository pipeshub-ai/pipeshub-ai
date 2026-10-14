"""Request validation and scope ITs for the connector-setup endpoints.

The cases come from fuzzing these operations with Schemathesis against
pipeshub-openapi.yaml: what the server refuses must be refused by the spec too
(so an SDK rejects it before sending), every documented status must be the one
returned, and a token holding the documented scopes must be let through.

Malformed and unvalidated input is pinned to what the server answers today
(500s and 200s, all documented), so the spec describes real responses. When the
server starts refusing such input with 400, these tests and the spec change together.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Sequence
from uuid import uuid4

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.clients.connectors_client import (  # noqa: E402
    ConnectorOAuthConfigsClient,
    ConnectorsClient,
)
from openapi_schema_validator import (  # noqa: E402
    assert_error_response_matches_openapi_operation,
    assert_operation_documents_response,
    operation_oauth2_scopes,
    operation_parameter_accepts,
    request_body_violations,
)

_REGISTRY = "getConnectorRegistry"
_CREATE = "createConnectorInstance"
_TEST_CONNECTION = "testConnectorConnection"
_FILTER_OPTIONS = "getFilterFieldOptions"
_UPDATE_AUTH = "updateConnectorAuthConfig"
_UPDATE_FILTERS_SYNC = "updateConnectorFiltersSyncConfig"
_TOGGLE = "toggleConnector"
_CALLBACK = "handleOAuthCallback"
_RESYNC = "resyncConnector"

def _send(client: ConnectorsClient, method: str, path: str, raw_json: str) -> requests.Response:
    return client.post(path, data=raw_json) if method == "POST" else client.put(path, data=raw_json)


@pytest.mark.integration
class TestRefusedRequestsAreDocumented:
    """Each request here is refused by the server; the spec must refuse it too."""

    def test_registry_unknown_scope(self, connectors_api: ConnectorsClient) -> None:
        resp = connectors_api.get_registry(scope="everyone")
        assert resp.status_code == 400, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _REGISTRY, 400)
        assert not operation_parameter_accepts(_REGISTRY, "scope", "everyone")

    def test_create_empty_connector_type(self, connectors_api: ConnectorsClient) -> None:
        body = {"connectorType": "", "instanceName": "connector-spec-it", "scope": "team"}
        resp = connectors_api.create_instance(body)
        assert resp.status_code == 400, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _CREATE, 400)
        assert request_body_violations(body, _CREATE), "the spec accepts an empty connectorType"

    def test_filter_options_page_zero(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        resp = connectors_api.get_filter_options(rss_connector.connector_id, "feed_urls", page=0)
        assert resp.status_code == 400, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _FILTER_OPTIONS, 400)
        assert not operation_parameter_accepts(_FILTER_OPTIONS, "page", 0)

    def test_filter_options_unsafe_filter_key(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        """A path segment that could escape the downstream service URL is refused."""
        resp = connectors_api.get_filter_options(rss_connector.connector_id, "a%b")
        assert resp.status_code == 400, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _FILTER_OPTIONS, 400)
        assert not operation_parameter_accepts(_FILTER_OPTIONS, "filterKey", "a%b")

    def test_filters_sync_empty_body(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        resp = connectors_api.update_filters_sync_config(rss_connector.connector_id, {})
        assert resp.status_code == 400, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _UPDATE_FILTERS_SYNC, 400)
        assert request_body_violations({}, _UPDATE_FILTERS_SYNC), "the spec accepts an empty body"

    def test_toggle_empty_device_id(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        body = {"type": "sync", "deviceId": ""}
        resp = connectors_api.toggle(rss_connector.connector_id, json=body)
        assert resp.status_code == 400, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _TOGGLE, 400)
        assert request_body_violations(body, _TOGGLE), "the spec accepts an empty deviceId"

    @pytest.mark.parametrize("missing", ["code", "state"])
    def test_callback_without_code_or_state(
        self, connectors_api: ConnectorsClient, missing: str
    ) -> None:
        params = {"code": "connector-spec-it-code", "state": "connector-spec-it-state"}
        params.pop(missing)
        resp = connectors_api.handle_oauth_callback(
            code=params.get("code"), state=params.get("state")
        )
        assert resp.status_code == 400, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _CALLBACK, 400)
        assert not operation_parameter_accepts(_CALLBACK, missing, None)


def _scope_cases() -> list[tuple[str, Callable[..., requests.Response]]]:
    """One call per operation that takes its success path, so every scope check on it runs."""
    return [
        ("getConnectorRegistry", lambda c, o, f, h: c.get_registry(scope="team", **h)),
        ("getConnectorSchema", lambda c, o, f, h: c.get_registry_schema("RSS", **h)),
        (
            "testConnectorConnection",
            lambda c, o, f, h: c.test_connection(
                "PostgreSQL", f["pg"].request_body["config"]["auth"], **h
            ),
        ),
        (
            "createConnectorInstance",
            lambda c, o, f, h: c.create_instance(
                {
                    "connectorType": "RSS",
                    "instanceName": f"connector-spec-it-rss-{uuid4().hex[:8]}",
                    "scope": "team",
                    "config": {"sync": {"feed_urls": f["feed"]}},
                },
                **h,
            ),
        ),
        ("getConnectorInstance", lambda c, o, f, h: c.get_instance(f["rss"].connector_id, **h)),
        ("getConnectorConfig", lambda c, o, f, h: c.get_config(f["rss"].connector_id, **h)),
        (
            "updateConnectorAuthConfig",
            lambda c, o, f, h: c.update_auth_config(
                f["pg"].connector_id, {"auth": f["pg"].request_body["config"]["auth"]}, **h
            ),
        ),
        (
            "updateConnectorFiltersSyncConfig",
            lambda c, o, f, h: c.update_filters_sync_config(
                f["rss"].connector_id, {"sync": {"selectedStrategy": "MANUAL"}}, **h
            ),
        ),
        (
            "getFilterFieldOptions",
            lambda c, o, f, h: c.get_filter_options(f["minio"].connector_id, "buckets", **h),
        ),
        ("toggleConnector", lambda c, o, f, h: c.toggle(f["rss"].connector_id, **h)),
        (
            "getOAuthAuthorizationUrl",
            lambda c, o, f, h: c.get_oauth_authorization_url(f["jira"].connector_id, **h),
        ),
        (
            "handleOAuthCallback",
            # A real state reaches the code exchange; the provider then refuses the code.
            lambda c, o, f, h: c.handle_oauth_callback(
                code="connector-spec-it-code",
                state=c.get_oauth_authorization_url(f["jira"].connector_id).json()["state"],
                **h,
            ),
        ),
        ("getConnectorEgressIps", lambda c, o, f, h: c.get_egress_ips(**h)),
        ("listConnectorInstances", lambda c, o, f, h: c.list_instances(scope="team", **h)),
        ("getConnectorStats", lambda c, o, f, h: c.get_stats(f["active"].connector_id, **h)),
        ("resyncConnector", lambda c, o, f, h: c.resync(f["active"].connector_id, "RSS", **h)),
        ("stopConnectorSync", lambda c, o, f, h: c.stop_sync(f["rss"].connector_id, **h)),
        (
            "deleteConnectorInstance",
            lambda c, o, f, h: c.delete_instance(
                c.create_instance(
                    {
                        "connectorType": "RSS",
                        "instanceName": f"connector-spec-it-rss-{uuid4().hex[:8]}",
                        "scope": "team",
                        "config": {"sync": {"feed_urls": f["feed"]}},
                    }
                ).json()["connector"]["connectorId"],
                **h,
            ),
        ),
        (
            "updateOAuthConfig",
            lambda c, o, f, h: o.update_config(
                "Jira",
                f["jira"].oauth_config_id,
                {"oauthInstanceName": f"connector-spec-it-jira-app-{uuid4().hex[:8]}"},
                **h,
            ),
        ),
        ("listOAuthConfigs", lambda c, o, f, h: o.list_all_configs(**h)),
        ("listOAuthConfigsByType", lambda c, o, f, h: o.list_configs("Jira", **h)),
        (
            "getOAuthConfig",
            lambda c, o, f, h: o.get_config("Jira", f["jira"].oauth_config_id, **h),
        ),
    ]


@pytest.mark.integration
class TestDocumentedScopes:
    @pytest.mark.parametrize(
        ("operation_id", "call"), _scope_cases(), ids=[op for op, _ in _scope_cases()]
    )
    def test_documented_scopes_are_enough(
        self,
        operation_id: str,
        call: Callable[..., requests.Response],
        connectors_api: ConnectorsClient,
        oauth_configs_api: ConnectorOAuthConfigsClient,
        rss_connector: Any,
        postgres_connector: Any,
        jira_oauth_connector: Any,
        minio_connector: Any,
        active_rss_connector: Any,
        rss_feed_url: str,
        rss_feed_reachable: None,
        scoped_token: Callable[[Sequence[str]], str],
        wait_until_idle: Callable[[str], None],
    ) -> None:
        """A token holding exactly the scopes the spec lists -- the ones a generated SDK
        requests -- gets the same success status as an all-scopes token."""
        documented = operation_oauth2_scopes(operation_id)
        assert documented, f"the spec lists no OAuth scopes for {operation_id}"
        token = scoped_token(documented)
        headers = {"auth": False, "headers": {"Authorization": f"Bearer {token}"}}
        fixtures = {
            "rss": rss_connector,
            "pg": postgres_connector,
            "jira": jira_oauth_connector,
            "minio": minio_connector,
            "active": active_rss_connector,
            "feed": rss_feed_url,
        }
        was_active = bool(
            connectors_api.get_instance(rss_connector.connector_id).json()["connector"]["isActive"]
        )

        resp = call(connectors_api, oauth_configs_api, fixtures, headers)
        try:
            expected = 202 if operation_id == "deleteConnectorInstance" else 200
            assert resp.status_code == expected, (
                f"with the spec's scopes {documented}, {operation_id} answered "
                f"{resp.status_code}: {resp.text}"
            )
        finally:
            if operation_id == "createConnectorInstance" and resp.status_code < 300:
                connectors_api.delete_instance(resp.json()["connector"]["connectorId"])
            if operation_id == "resyncConnector":
                wait_until_idle(active_rss_connector.connector_id)
            if operation_id == "toggleConnector":
                now = connectors_api.get_instance(rss_connector.connector_id).json()["connector"]
                if bool(now["isActive"]) != was_active:
                    connectors_api.toggle(rss_connector.connector_id)


    @pytest.mark.parametrize(
        ("operation_id", "read"),
        [
            # Saving `sync` fires a background schedule reconcile that reads the config
            # with the caller's token; a refused read is only logged, never returned.
            ("updateConnectorFiltersSyncConfig", "get_config"),
            # Toggle reads the instance with the caller's token before flipping it.
            ("toggleConnector", "get_instance"),
        ],
    )
    def test_documented_scopes_cover_the_reads_made_with_the_callers_token(
        self,
        operation_id: str,
        read: str,
        connectors_api: ConnectorsClient,
        rss_connector: Any,
        scoped_token: Callable[[Sequence[str]], str],
    ) -> None:
        documented = operation_oauth2_scopes(operation_id)
        token = scoped_token(documented)
        resp = getattr(connectors_api, read)(
            rss_connector.connector_id, auth=False, headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200, (
            f"{operation_id} reads with the caller's token, but its documented scopes "
            f"{documented} cannot: {resp.status_code} {resp.text}"
        )


@pytest.mark.integration
class TestMalformedBodies:
    """A body of the wrong JSON type is answered with the documented 500."""

    @pytest.mark.parametrize(
        ("operation_id", "method", "path"),
        [
            (_CREATE, "POST", "/"),
            (_TEST_CONNECTION, "POST", "/registry/PostgreSQL/test-connection"),
            (_TOGGLE, "POST", "/{rss}/toggle"),
            (_UPDATE_AUTH, "PUT", "/{pg}/config/auth"),
            (_UPDATE_FILTERS_SYNC, "PUT", "/{rss}/config/filters-sync"),
            (_RESYNC, "POST", "/{active}/resync"),
        ],
        ids=[_CREATE, _TEST_CONNECTION, _TOGGLE, _UPDATE_AUTH, _UPDATE_FILTERS_SYNC, _RESYNC],
    )
    def test_body_that_is_not_an_object(
        self,
        connectors_api: ConnectorsClient,
        rss_connector: Any,
        postgres_connector: Any,
        active_rss_connector: Any,
        operation_id: str,
        method: str,
        path: str,
    ) -> None:
        path = path.format(
            rss=rss_connector.connector_id,
            pg=postgres_connector.connector_id,
            active=active_rss_connector.connector_id,
        )
        resp = _send(connectors_api, method, path, '"not-an-object"')
        assert resp.status_code == 500, f"{resp.status_code}: {resp.text}"
        assert_error_response_matches_openapi_operation(resp.json(), operation_id, 500)

    def test_update_auth_with_array_auth(
        self, connectors_api: ConnectorsClient, postgres_connector: Any
    ) -> None:
        resp = connectors_api.update_auth_config(postgres_connector.connector_id, {"auth": ["x"]})
        assert resp.status_code == 500, f"{resp.status_code}: {resp.text}"
        assert_error_response_matches_openapi_operation(resp.json(), _UPDATE_AUTH, 500)

    def test_filters_sync_with_array_filters(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        resp = connectors_api.update_filters_sync_config(
            rss_connector.connector_id, {"filters": [None]}
        )
        assert resp.status_code == 500, f"{resp.status_code}: {resp.text}"
        assert_error_response_matches_openapi_operation(resp.json(), _UPDATE_FILTERS_SYNC, 500)


@pytest.mark.integration
class TestUnvalidatedInput:
    """Input outside the documented request schema that the server still saves (200)."""

    def test_update_auth_with_object_oauth_config_id(
        self, connectors_api: ConnectorsClient, postgres_connector: Any
    ) -> None:
        auth = postgres_connector.request_body["config"]["auth"]
        body = {"auth": {**auth, "oauthConfigId": {"x": 1}}}
        assert request_body_violations(body, _UPDATE_AUTH)
        try:
            resp = connectors_api.update_auth_config(postgres_connector.connector_id, body)
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
            assert_operation_documents_response(_UPDATE_AUTH, "200")
        finally:
            connectors_api.update_auth_config(
                postgres_connector.connector_id, {"auth": {**auth, "oauthConfigId": ""}}
            )

    def test_filters_sync_with_string_sync(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        body = {"sync": "MANUAL"}
        assert request_body_violations(body, _UPDATE_FILTERS_SYNC)
        try:
            resp = connectors_api.update_filters_sync_config(rss_connector.connector_id, body)
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
            assert_operation_documents_response(_UPDATE_FILTERS_SYNC, "200")
        finally:
            connectors_api.update_filters_sync_config(
                rss_connector.connector_id, {"sync": {"selectedStrategy": "MANUAL"}}
            )
