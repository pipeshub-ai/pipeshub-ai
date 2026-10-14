"""Response-validation ITs for connecting a source that signs in with OAuth.

- POST /api/v1/connectors                              (OAuth app credentials inline)
- GET  /api/v1/oauth/{connectorType}                   (saved OAuth apps)
- GET  /api/v1/oauth/{connectorType}/{configId}
- GET  /api/v1/connectors/{connectorId}/oauth/authorize
- GET  /api/v1/connectors/oauth/callback
- POST /api/v1/connectors/{connectorId}/toggle         (before sign-in completes)

The provider's consent screen needs a person, so the callback is driven with a
code the provider rejects: that still exercises the response an SDK caller
gets back. Uses Jira, whose authorize URL is built without calling Atlassian.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest

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
    assert_operation_requires_authentication,
    assert_query_params_documented,
    assert_request_body_strictly_matches_openapi_operation,
    assert_response_strictly_matches_openapi_operation,
    operation_oauth2_scopes,
    operation_request_examples,
)

_CREATE = "createConnectorInstance"
_LIST_OAUTH_CONFIGS = "listOAuthConfigsByType"
_GET_OAUTH_CONFIG = "getOAuthConfig"
_AUTHORIZE = "getOAuthAuthorizationUrl"
_CALLBACK = "handleOAuthCallback"
_TOGGLE = "toggleConnector"

@pytest.mark.integration
class TestCreateOAuthConnector:
    def test_create_with_oauth_app_credentials_request_body_matches_spec(
        self, jira_oauth_connector: Any
    ) -> None:
        """An admin passes the OAuth app's client ID and secret in ``config.auth``."""
        assert_request_body_strictly_matches_openapi_operation(
            jira_oauth_connector.request_body, _CREATE
        )

    def test_create_with_oauth_app_credentials_saves_and_links_the_app(
        self,
        connectors_api: ConnectorsClient,
        oauth_configs_api: ConnectorOAuthConfigsClient,
        jira_oauth_connector: Any,
    ) -> None:
        """No separate POST /api/v1/oauth/{connectorType} is needed."""
        app = oauth_configs_api.get_config("Jira", jira_oauth_connector.oauth_config_id)
        assert app.status_code == 200, app.text

        config = connectors_api.get_config(jira_oauth_connector.connector_id)
        assert config.status_code == 200, config.text
        sent_secret = jira_oauth_connector.request_body["config"]["auth"]["clientSecret"]
        assert sent_secret not in config.text, "the connector config echoed the client secret"

    def test_create_with_existing_oauth_app(
        self,
        connectors_api: ConnectorsClient,
        jira_oauth_connector: Any,
        linked_oauth_config_id: Callable[[str], str | None],
    ) -> None:
        """Non-admins must pick a saved OAuth app; its ID goes in ``config.auth``."""
        body = {
            "connectorType": "Jira",
            "instanceName": f"connector-spec-it-jira-{uuid4().hex[:8]}",
            "scope": "team",
            "authType": "OAUTH",
            "baseUrl": jira_oauth_connector.request_body["baseUrl"],
            "config": {"auth": {"oauthConfigId": jira_oauth_connector.oauth_config_id}},
        }
        resp = connectors_api.create_instance(body)
        assert resp.status_code < 300, resp.text
        connector_id = resp.json()["connector"]["connectorId"]
        try:
            assert linked_oauth_config_id(connector_id) == jira_oauth_connector.oauth_config_id
            assert_request_body_strictly_matches_openapi_operation(body, _CREATE)
        finally:
            connectors_api.delete_instance(connector_id)

    def test_top_level_oauth_config_id_is_not_part_of_the_contract(
        self,
        connectors_api: ConnectorsClient,
        jira_oauth_connector: Any,
        linked_oauth_config_id: Callable[[str], str | None],
    ) -> None:
        """The server ignores a top-level ``oauthConfigId``, so the spec must not offer it."""
        body = {
            "connectorType": "Jira",
            "instanceName": f"connector-spec-it-jira-{uuid4().hex[:8]}",
            "scope": "team",
            "authType": "OAUTH",
            "baseUrl": jira_oauth_connector.request_body["baseUrl"],
            "oauthConfigId": jira_oauth_connector.oauth_config_id,
        }
        resp = connectors_api.create_instance(body)
        assert resp.status_code < 300, resp.text
        connector_id = resp.json()["connector"]["connectorId"]
        try:
            assert linked_oauth_config_id(connector_id) is None
        finally:
            connectors_api.delete_instance(connector_id)

        with pytest.raises(AssertionError, match="oauthConfigId"):
            assert_request_body_strictly_matches_openapi_operation(body, _CREATE)


    @pytest.mark.parametrize("example", sorted(operation_request_examples(_CREATE)))
    def test_documented_create_example_is_accepted(
        self,
        connectors_api: ConnectorsClient,
        oauth_configs_api: ConnectorOAuthConfigsClient,
        jira_oauth_connector: Any,
        linked_oauth_config_id: Callable[[str], str | None],
        example: str,
    ) -> None:
        """Each example in the spec is a request the server accepts as it stands."""
        body = operation_request_examples(_CREATE)[example]
        body["instanceName"] = f"connector-spec-it-example-{uuid4().hex[:8]}"
        auth = (body.get("config") or {}).get("auth") or {}
        if "oauthConfigId" in auth:
            auth["oauthConfigId"] = jira_oauth_connector.oauth_config_id
        if "oauthInstanceName" in auth:
            auth["oauthInstanceName"] = f"connector-spec-it-example-app-{uuid4().hex[:8]}"

        resp = connectors_api.create_instance(body)
        assert resp.status_code < 300, f"example {example!r}: {resp.status_code} {resp.text}"
        connector_id = resp.json()["connector"]["connectorId"]
        new_app = None
        try:
            linked = linked_oauth_config_id(connector_id)
            if "clientSecret" in auth:
                new_app = linked
                assert new_app, "the inline OAuth app was not saved and linked"
            elif "oauthConfigId" in auth:
                assert linked == jira_oauth_connector.oauth_config_id
            assert_request_body_strictly_matches_openapi_operation(body, _CREATE)
        finally:
            connectors_api.delete_instance(connector_id)
            if new_app:
                oauth_configs_api.delete_config(body["connectorType"], new_app)


@pytest.mark.integration
class TestSavedOAuthApps:
    def test_list_oauth_configs_matches_spec(
        self, oauth_configs_api: ConnectorOAuthConfigsClient, jira_oauth_connector: Any
    ) -> None:
        params = {"page": 1, "limit": 100}
        assert_query_params_documented(_LIST_OAUTH_CONFIGS, params)

        resp = oauth_configs_api.list_configs("Jira", **params)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("oauthConfigs"), f"the saved OAuth app is not listed: {body}"

        assert_response_strictly_matches_openapi_operation(body, _LIST_OAUTH_CONFIGS)

    def test_list_all_oauth_configs_matches_spec(
        self, oauth_configs_api: ConnectorOAuthConfigsClient, jira_oauth_connector: Any
    ) -> None:
        """Every connector type's OAuth apps; this list counts ``totalItems``."""
        listed: set[str] = set()
        page = 1
        while True:
            resp = oauth_configs_api.list_all_configs(page=page, limit=100)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert_response_strictly_matches_openapi_operation(body, "listOAuthConfigs")
            listed |= {c.get("_id") for c in body["oauthConfigs"]}
            if jira_oauth_connector.oauth_config_id in listed or not body["pagination"]["hasNext"]:
                break
            page += 1
        assert jira_oauth_connector.oauth_config_id in listed, "the saved Jira app is not listed"

    def test_get_oauth_config_matches_spec(
        self, oauth_configs_api: ConnectorOAuthConfigsClient, jira_oauth_connector: Any
    ) -> None:
        resp = oauth_configs_api.get_config("Jira", jira_oauth_connector.oauth_config_id)
        assert resp.status_code == 200, resp.text
        assert_response_strictly_matches_openapi_operation(resp.json(), _GET_OAUTH_CONFIG)

    def test_get_unknown_oauth_config_is_404(
        self, oauth_configs_api: ConnectorOAuthConfigsClient
    ) -> None:
        resp = oauth_configs_api.get_config("Jira", str(uuid4()))
        assert resp.status_code == 404, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _GET_OAUTH_CONFIG, 404)

    @pytest.mark.parametrize("operation_id", [_LIST_OAUTH_CONFIGS, _GET_OAUTH_CONFIG])
    def test_oauth_config_reads_document_their_scope(
        self,
        oauth_configs_api: ConnectorOAuthConfigsClient,
        jira_oauth_connector: Any,
        scoped_token: Callable[[Sequence[str]], str],
        operation_id: str,
    ) -> None:
        """A token without ``connector:read`` is refused, so the spec must say it is needed."""
        headers = {"Authorization": f"Bearer {scoped_token(['kb:read'])}"}
        if operation_id == _LIST_OAUTH_CONFIGS:
            resp = oauth_configs_api.list_configs("Jira", auth=False, headers=headers)
        else:
            resp = oauth_configs_api.get_config(
                "Jira", jira_oauth_connector.oauth_config_id, auth=False, headers=headers
            )
        assert resp.status_code == 403, resp.text

        documented = operation_oauth2_scopes(operation_id)
        assert "connector:read" in documented, (
            f"{operation_id} refuses tokens without connector:read, but the spec "
            f"documents scopes {documented}"
        )


@pytest.mark.integration
class TestOAuthSignIn:
    def test_authorize_matches_spec(
        self, connectors_api: ConnectorsClient, jira_oauth_connector: Any
    ) -> None:
        resp = connectors_api.get_oauth_authorization_url(jira_oauth_connector.connector_id)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        sent = jira_oauth_connector.request_body
        query = parse_qs(urlparse(body["authorizationUrl"]).query)
        assert query.get("client_id") == [sent["config"]["auth"]["clientId"]], query
        # The redirect URI comes from the baseUrl given when the connector was created.
        assert query.get("redirect_uri") == [
            f"{sent['baseUrl']}/connectors/oauth/callback/Jira"
        ], query
        assert body["state"], body

        assert_response_strictly_matches_openapi_operation(body, _AUTHORIZE)

    def test_callback_requires_authentication(self, connectors_api: ConnectorsClient) -> None:
        resp = connectors_api.handle_oauth_callback(
            code="connector-spec-it-code", state="connector-spec-it-state", auth=False
        )
        assert resp.status_code == 401, resp.text

        assert_operation_requires_authentication(_CALLBACK)
        assert_error_response_matches_openapi_operation(resp.json(), _CALLBACK, 401)

    def test_callback_response_matches_spec(
        self, connectors_api: ConnectorsClient, jira_oauth_connector: Any
    ) -> None:
        """The caller hands back the provider's code and state and reads the result as JSON."""
        authorize = connectors_api.get_oauth_authorization_url(jira_oauth_connector.connector_id)
        assert authorize.status_code == 200, authorize.text
        params = {
            "code": "connector-spec-it-rejected-code",
            "state": authorize.json()["state"],
            "baseUrl": jira_oauth_connector.request_body["baseUrl"],
        }
        assert_query_params_documented(_CALLBACK, params)

        resp = connectors_api.handle_oauth_callback(
            code=params["code"], state=params["state"], base_url=params["baseUrl"]
        )
        assert resp.status_code < 500, resp.text
        body = resp.json()
        assert body.get("success") is False, f"a rejected code must not sign in: {body}"

        assert_response_strictly_matches_openapi_operation(
            body, _CALLBACK, status_code=str(resp.status_code)
        )

    def test_toggle_before_sign_in_is_400(
        self, connectors_api: ConnectorsClient, jira_oauth_connector: Any
    ) -> None:
        """Sync cannot be enabled until the OAuth sign-in has stored tokens."""
        resp = connectors_api.toggle(jira_oauth_connector.connector_id)
        assert resp.status_code == 400, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _TOGGLE, 400)
