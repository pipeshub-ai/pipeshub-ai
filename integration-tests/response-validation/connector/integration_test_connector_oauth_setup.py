"""Response-validation ITs for connecting a source that signs in with OAuth.

- POST /api/v1/connectors                              (OAuth app credentials inline)
- GET  /api/v1/oauth/{connectorType}                   (saved OAuth apps)
- GET  /api/v1/oauth/{connectorType}/{configId}
- PUT  /api/v1/oauth/{connectorType}/{configId}         (rename, rotate the secret)
- POST /api/v1/oauth/{connectorType}                   (save an OAuth app on its own)
- DELETE /api/v1/oauth/{connectorType}/{configId}
- GET  /api/v1/oauth/registry                          (connector types that use an OAuth app)
- GET  /api/v1/oauth/registry/{connectorType}
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
from typing import Any, Callable, Iterator, Sequence
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.clients.config_client import ConfigClient  # noqa: E402
from helper.clients.connectors_client import (  # noqa: E402
    ConnectorOAuthConfigsClient,
    ConnectorsClient,
)
from openapi_schema_validator import (  # noqa: E402
    _operation_responses,
    assert_error_response_matches_openapi_operation,
    assert_operation_requires_authentication,
    assert_query_params_documented,
    assert_request_body_strictly_matches_openapi_operation,
    assert_response_strictly_matches_openapi_operation,
    load_openapi_document,
    operation_oauth2_scopes,
    operation_request_examples,
    request_body_violations,
)

_CREATE = "createConnectorInstance"
_LIST_ALL_OAUTH_CONFIGS = "listOAuthConfigs"
_LIST_OAUTH_CONFIGS = "listOAuthConfigsByType"
_GET_OAUTH_CONFIG = "getOAuthConfig"
_UPDATE_OAUTH_CONFIG = "updateOAuthConfig"
_CREATE_OAUTH_CONFIG = "createOAuthConfig"
_DELETE_OAUTH_CONFIG = "deleteOAuthConfig"
_OAUTH_REGISTRY = "getOAuthRegistry"
_OAUTH_REGISTRY_ENTRY = "getOAuthConnectorType"
_AUTHORIZE = "getOAuthAuthorizationUrl"
_CALLBACK = "handleOAuthCallback"
_TOGGLE = "toggleConnector"

JIRA_DUMMY_CLIENT_ID = "connector-spec-it-client-id"
OAUTH_BASE_URL = "http://localhost:3001"

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
            assert_response_strictly_matches_openapi_operation(body, _LIST_ALL_OAUTH_CONFIGS)
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

    def test_update_oauth_config_matches_spec(
        self, oauth_configs_api: ConnectorOAuthConfigsClient, jira_oauth_connector: Any
    ) -> None:
        """Rename the app and rotate its client secret."""
        client_id = jira_oauth_connector.request_body["config"]["auth"]["clientId"]
        body = {
            "oauthInstanceName": f"connector-spec-it-jira-app-{uuid4().hex[:8]}",
            "config": {"clientId": client_id, "clientSecret": f"secret-{uuid4().hex}"},
        }
        assert_request_body_strictly_matches_openapi_operation(body, _UPDATE_OAUTH_CONFIG)

        resp = oauth_configs_api.update_config("Jira", jira_oauth_connector.oauth_config_id, body)
        assert resp.status_code == 200, resp.text
        updated = resp.json()
        assert updated["oauthConfig"]["oauthInstanceName"] == body["oauthInstanceName"], updated
        assert_response_strictly_matches_openapi_operation(updated, _UPDATE_OAUTH_CONFIG)

    @pytest.mark.parametrize("body", [{}, {"baseUrl": "http://localhost:3001"}])
    def test_update_oauth_config_without_changes_is_400(
        self,
        oauth_configs_api: ConnectorOAuthConfigsClient,
        jira_oauth_connector: Any,
        body: dict[str, Any],
    ) -> None:
        """Neither ``oauthInstanceName`` nor ``config``: nothing to update."""
        resp = oauth_configs_api.update_config("Jira", jira_oauth_connector.oauth_config_id, body)
        assert resp.status_code == 400, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _UPDATE_OAUTH_CONFIG, 400)
        assert request_body_violations(body, _UPDATE_OAUTH_CONFIG), (
            f"the spec accepts {body} with nothing to update"
        )

    def test_update_oauth_config_with_empty_name_is_400(
        self, oauth_configs_api: ConnectorOAuthConfigsClient, jira_oauth_connector: Any
    ) -> None:
        body = {"oauthInstanceName": ""}
        resp = oauth_configs_api.update_config("Jira", jira_oauth_connector.oauth_config_id, body)
        assert resp.status_code == 400, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _UPDATE_OAUTH_CONFIG, 400)
        assert request_body_violations(body, _UPDATE_OAUTH_CONFIG), (
            "the spec accepts an empty oauthInstanceName"
        )

    def test_update_oauth_config_body_that_is_not_an_object_is_400(
        self, oauth_configs_api: ConnectorOAuthConfigsClient, jira_oauth_connector: Any
    ) -> None:
        resp = oauth_configs_api.put(
            f"/Jira/{jira_oauth_connector.oauth_config_id}",
            data='"not-an-object"',
        )
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"
        assert resp.json()["error"]["message"] == "The request body is not valid JSON."
        assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST"
        assert_error_response_matches_openapi_operation(resp.json(), _UPDATE_OAUTH_CONFIG, 400)

    def test_update_oauth_config_with_array_config_is_500(
        self, oauth_configs_api: ConnectorOAuthConfigsClient, jira_oauth_connector: Any
    ) -> None:
        body = {"config": [None]}
        assert request_body_violations(body, _UPDATE_OAUTH_CONFIG)
        resp = oauth_configs_api.update_config("Jira", jira_oauth_connector.oauth_config_id, body)
        assert resp.status_code == 500, f"{resp.status_code}: {resp.text}"
        assert_error_response_matches_openapi_operation(resp.json(), _UPDATE_OAUTH_CONFIG, 500)

    def test_update_oauth_config_saves_a_non_string_tenant_id(
        self, oauth_configs_api: ConnectorOAuthConfigsClient, jira_oauth_connector: Any
    ) -> None:
        """Outside the documented request schema, yet saved (200)."""
        body = {"config": {"tenantId": {"x": 1}}}
        assert request_body_violations(body, _UPDATE_OAUTH_CONFIG)
        try:
            resp = oauth_configs_api.update_config(
                "Jira", jira_oauth_connector.oauth_config_id, body
            )
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
            assert_response_strictly_matches_openapi_operation(resp.json(), _UPDATE_OAUTH_CONFIG)
        finally:
            oauth_configs_api.update_config(
                "Jira", jira_oauth_connector.oauth_config_id, {"config": {"tenantId": ""}}
            )

    def test_update_oauth_config_to_a_taken_name_is_409(
        self,
        connectors_api: ConnectorsClient,
        oauth_configs_api: ConnectorOAuthConfigsClient,
        jira_oauth_connector: Any,
        linked_oauth_config_id: Callable[[str], str | None],
    ) -> None:
        taken = f"connector-spec-it-jira-app-{uuid4().hex[:8]}"
        auth = dict(jira_oauth_connector.request_body["config"]["auth"], oauthInstanceName=taken)
        body = {
            **jira_oauth_connector.request_body,
            "instanceName": f"connector-spec-it-jira-{uuid4().hex[:8]}",
            "config": {"auth": auth},
        }
        created = connectors_api.create_instance(body)
        assert created.status_code < 300, created.text
        connector_id = created.json()["connector"]["connectorId"]
        other_app = None
        try:
            other_app = linked_oauth_config_id(connector_id)
            assert other_app, "the second OAuth app was not saved"
            resp = oauth_configs_api.update_config(
                "Jira", jira_oauth_connector.oauth_config_id, {"oauthInstanceName": taken}
            )
            assert resp.status_code == 409, resp.text
            assert_error_response_matches_openapi_operation(resp.json(), _UPDATE_OAUTH_CONFIG, 409)
        finally:
            connectors_api.delete_instance(connector_id)
            if other_app:
                oauth_configs_api.delete_config("Jira", other_app)

    def test_update_unknown_oauth_config_is_404(
        self, oauth_configs_api: ConnectorOAuthConfigsClient
    ) -> None:
        resp = oauth_configs_api.update_config(
            "Jira", str(uuid4()), {"oauthInstanceName": "connector-spec-it-missing"}
        )
        assert resp.status_code == 404, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _UPDATE_OAUTH_CONFIG, 404)

    def test_update_oauth_config_unauthenticated_is_401(
        self, oauth_configs_api: ConnectorOAuthConfigsClient, jira_oauth_connector: Any
    ) -> None:
        resp = oauth_configs_api.update_config(
            "Jira", jira_oauth_connector.oauth_config_id, {"oauthInstanceName": "x"}, auth=False
        )
        assert resp.status_code == 401, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _UPDATE_OAUTH_CONFIG, 401)

    @pytest.mark.parametrize(
        "operation_id", [_LIST_ALL_OAUTH_CONFIGS, _LIST_OAUTH_CONFIGS, _GET_OAUTH_CONFIG]
    )
    def test_oauth_config_reads_document_their_scope(
        self,
        oauth_configs_api: ConnectorOAuthConfigsClient,
        jira_oauth_connector: Any,
        scoped_token: Callable[[Sequence[str]], str],
        operation_id: str,
    ) -> None:
        """A token without ``connector:read`` is refused, so the spec must say it is needed."""
        headers = {"Authorization": f"Bearer {scoped_token(['kb:read'])}"}
        if operation_id == _LIST_ALL_OAUTH_CONFIGS:
            resp = oauth_configs_api.list_all_configs(auth=False, headers=headers)
        elif operation_id == _LIST_OAUTH_CONFIGS:
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


@pytest.fixture
def throwaway_jira_app(
    oauth_configs_api: ConnectorOAuthConfigsClient,
) -> Iterator[dict[str, Any]]:
    """A Jira OAuth app saved on its own with dummy credentials, deleted afterwards."""
    body = {
        "oauthInstanceName": f"connector-spec-it-jira-app-{uuid4().hex[:8]}",
        "config": {"clientId": JIRA_DUMMY_CLIENT_ID, "clientSecret": f"secret-{uuid4().hex}"},
    }
    resp = oauth_configs_api.create_config("Jira", body)
    assert resp.status_code == 200, resp.text
    app = {"body": body, "id": resp.json()["oauthConfig"]["_id"]}
    try:
        yield app
    finally:
        oauth_configs_api.delete_config("Jira", app["id"])


@pytest.mark.integration
class TestCreateAndDeleteOAuthApp:
    def test_create_oauth_config_matches_spec(
        self, oauth_configs_api: ConnectorOAuthConfigsClient
    ) -> None:
        """200 rather than 201, with a ``message`` beside ``success`` and ``oauthConfig``."""
        body = {
            "oauthInstanceName": f"connector-spec-it-jira-app-{uuid4().hex[:8]}",
            "config": {"clientId": JIRA_DUMMY_CLIENT_ID, "clientSecret": f"secret-{uuid4().hex}"},
            "baseUrl": "https://pipeshub.example.com",
        }
        assert_request_body_strictly_matches_openapi_operation(body, _CREATE_OAUTH_CONFIG)

        resp = oauth_configs_api.create_config("Jira", body)
        config_id = None
        try:
            assert resp.status_code == 200, resp.text
            created = resp.json()
            oauth_config = created.get("oauthConfig")
            assert isinstance(oauth_config, dict), created
            config_id = oauth_config.get("_id")
            assert created["success"] is True and created["message"], created
            assert oauth_config["oauthInstanceName"] == body["oauthInstanceName"]
            assert oauth_config["connectorType"] == "Jira"
            assert "config" not in oauth_config, "the create response echoed credentials"
            assert_response_strictly_matches_openapi_operation(created, _CREATE_OAUTH_CONFIG)

            saved = oauth_configs_api.get_config("Jira", config_id)
            assert saved.status_code == 200, saved.text
            assert saved.json()["oauthConfig"]["oauthInstanceName"] == body["oauthInstanceName"]
        finally:
            if config_id:
                oauth_configs_api.delete_config("Jira", config_id)

    def test_create_oauth_config_with_a_taken_name_is_409(
        self, oauth_configs_api: ConnectorOAuthConfigsClient, throwaway_jira_app: dict[str, Any]
    ) -> None:
        resp = oauth_configs_api.create_config("Jira", throwaway_jira_app["body"])
        assert resp.status_code == 409, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _CREATE_OAUTH_CONFIG, 409)

    @pytest.mark.parametrize(
        "body",
        [
            {"config": {"clientId": "a", "clientSecret": "b"}},
            {"oauthInstanceName": ""},
            {"oauthInstanceName": "connector-spec-it-no-config"},
            {"oauthInstanceName": "connector-spec-it-empty-config", "config": {}},
        ],
        ids=["no-name", "empty-name", "no-config", "empty-config"],
    )
    def test_create_oauth_config_without_name_or_config_is_400(
        self, oauth_configs_api: ConnectorOAuthConfigsClient, body: dict[str, Any]
    ) -> None:
        resp = oauth_configs_api.create_config("Jira", body)
        assert resp.status_code == 400, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _CREATE_OAUTH_CONFIG, 400)
        assert request_body_violations(body, _CREATE_OAUTH_CONFIG), (
            f"the spec accepts {body}"
        )

    def test_create_oauth_config_body_that_is_not_an_object_is_400(
        self, oauth_configs_api: ConnectorOAuthConfigsClient
    ) -> None:
        resp = oauth_configs_api.post("/Jira", data='"not-an-object"')
        assert resp.status_code == 400, f"{resp.status_code}: {resp.text}"
        assert resp.json()["error"]["message"] == "The request body is not valid JSON."
        assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST"
        assert_error_response_matches_openapi_operation(resp.json(), _CREATE_OAUTH_CONFIG, 400)

    def test_create_oauth_config_unauthenticated_is_401(
        self, oauth_configs_api: ConnectorOAuthConfigsClient
    ) -> None:
        body = {"oauthInstanceName": "connector-spec-it-x", "config": {"clientId": "a"}}
        resp = oauth_configs_api.create_config("Jira", body, auth=False)
        assert resp.status_code == 401, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _CREATE_OAUTH_CONFIG, 401)

    def test_create_oauth_config_documents_and_needs_exactly_connector_write(
        self,
        oauth_configs_api: ConnectorOAuthConfigsClient,
        scoped_token: Callable[[Sequence[str]], str],
    ) -> None:
        assert operation_oauth2_scopes(_CREATE_OAUTH_CONFIG) == ["connector:write"]

        def create(scope: str, name: str) -> Any:
            headers = {"Authorization": f"Bearer {scoped_token([scope])}"}
            body = {
                "oauthInstanceName": name,
                "config": {"clientId": JIRA_DUMMY_CLIENT_ID, "clientSecret": "dummy"},
            }
            return oauth_configs_api.create_config("Jira", body, auth=False, headers=headers)

        refused = create("connector:read", f"connector-spec-it-jira-app-{uuid4().hex[:8]}")
        assert refused.status_code == 403, refused.text
        assert_error_response_matches_openapi_operation(refused.json(), _CREATE_OAUTH_CONFIG, 403)

        created = create("connector:write", f"connector-spec-it-jira-app-{uuid4().hex[:8]}")
        try:
            assert created.status_code == 200, created.text
        finally:
            if created.status_code == 200:
                oauth_configs_api.delete_config("Jira", created.json()["oauthConfig"]["_id"])

    def test_delete_oauth_config_matches_spec(
        self, oauth_configs_api: ConnectorOAuthConfigsClient
    ) -> None:
        created = oauth_configs_api.create_config(
            "Jira",
            {
                "oauthInstanceName": f"connector-spec-it-jira-app-{uuid4().hex[:8]}",
                "config": {"clientId": JIRA_DUMMY_CLIENT_ID, "clientSecret": "dummy"},
            },
        )
        assert created.status_code == 200, created.text
        config_id = created.json()["oauthConfig"]["_id"]

        resp = oauth_configs_api.delete_config("Jira", config_id)
        assert resp.status_code == 200, resp.text
        assert config_id in resp.json()["message"], resp.json()
        assert_response_strictly_matches_openapi_operation(resp.json(), _DELETE_OAUTH_CONFIG)

        gone = oauth_configs_api.get_config("Jira", config_id)
        assert gone.status_code == 404, gone.text

        again = oauth_configs_api.delete_config("Jira", config_id)
        assert again.status_code == 404, again.text
        assert_error_response_matches_openapi_operation(again.json(), _DELETE_OAUTH_CONFIG, 404)

    def test_delete_unknown_oauth_config_is_404(
        self, oauth_configs_api: ConnectorOAuthConfigsClient
    ) -> None:
        resp = oauth_configs_api.delete_config("Jira", str(uuid4()))
        assert resp.status_code == 404, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _DELETE_OAUTH_CONFIG, 404)

    def test_delete_oauth_config_used_by_a_connector_is_allowed(
        self,
        connectors_api: ConnectorsClient,
        oauth_configs_api: ConnectorOAuthConfigsClient,
        throwaway_jira_app: dict[str, Any],
        linked_oauth_config_id: Callable[[str], str | None],
    ) -> None:
        """No 400: the server does not check for connectors that still use the app."""
        body = {
            "connectorType": "Jira",
            "instanceName": f"connector-spec-it-jira-{uuid4().hex[:8]}",
            "scope": "team",
            "authType": "OAUTH",
            "baseUrl": OAUTH_BASE_URL,
            "config": {"auth": {"oauthConfigId": throwaway_jira_app["id"]}},
        }
        created = connectors_api.create_instance(body)
        assert created.status_code < 300, created.text
        connector_id = created.json()["connector"]["connectorId"]
        try:
            assert linked_oauth_config_id(connector_id) == throwaway_jira_app["id"]
            resp = oauth_configs_api.delete_config("Jira", throwaway_jira_app["id"])
            assert resp.status_code == 200, resp.text
            assert_response_strictly_matches_openapi_operation(resp.json(), _DELETE_OAUTH_CONFIG)
            # The only documented 400 is the app-wide markup filter, not "still in use".
            documented = _operation_responses(_DELETE_OAUTH_CONFIG).get("400")
            assert documented == {"$ref": "#/components/responses/XssRejected"}, documented
        finally:
            connectors_api.delete_instance(connector_id)

    def test_delete_oauth_config_with_markup_in_a_query_value_is_400(
        self, oauth_configs_api: ConnectorOAuthConfigsClient
    ) -> None:
        """The app-wide XSS filter answers before authentication, as the XssRejected ref says."""
        assert _operation_responses(_DELETE_OAUTH_CONFIG).get("400") == {
            "$ref": "#/components/responses/XssRejected"
        }
        # The validator never reads descriptions, so the shared response's sentence is checked here.
        xss = load_openapi_document()["components"]["responses"]["XssRejected"]["description"]
        assert "HTML tags, scripts, and XSS content are not allowed" in xss, xss
        resp = oauth_configs_api.delete_config(
            "Jira", str(uuid4()), auth=False, params={"q": "<script>alert(1)</script>"}
        )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert_error_response_matches_openapi_operation(body, _DELETE_OAUTH_CONFIG, 400)
        assert body["error"]["code"] == "HTTP_BAD_REQUEST", body
        assert body["error"]["message"].startswith(
            'HTML tags, scripts, and XSS content are not allowed in the query parameter "q".'
        ), body

    def test_delete_oauth_config_unauthenticated_is_401(
        self, oauth_configs_api: ConnectorOAuthConfigsClient, throwaway_jira_app: dict[str, Any]
    ) -> None:
        resp = oauth_configs_api.delete_config("Jira", throwaway_jira_app["id"], auth=False)
        assert resp.status_code == 401, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _DELETE_OAUTH_CONFIG, 401)

    def test_delete_oauth_config_documents_and_needs_exactly_connector_delete(
        self,
        oauth_configs_api: ConnectorOAuthConfigsClient,
        throwaway_jira_app: dict[str, Any],
        scoped_token: Callable[[Sequence[str]], str],
    ) -> None:
        assert operation_oauth2_scopes(_DELETE_OAUTH_CONFIG) == ["connector:delete"]

        def delete(scope: str) -> Any:
            headers = {"Authorization": f"Bearer {scoped_token([scope])}"}
            return oauth_configs_api.delete_config(
                "Jira", throwaway_jira_app["id"], auth=False, headers=headers
            )

        refused = delete("connector:write")
        assert refused.status_code == 403, refused.text
        assert_error_response_matches_openapi_operation(refused.json(), _DELETE_OAUTH_CONFIG, 403)

        allowed = delete("connector:delete")
        assert allowed.status_code == 200, allowed.text


@pytest.mark.integration
class TestOAuthRegistry:
    def test_registry_matches_spec(self, oauth_configs_api: ConnectorOAuthConfigsClient) -> None:
        """Every connector type, across pages; each entry carries the full OAuth description."""
        params = {"page": 1, "limit": 10, "search": "a"}
        assert_query_params_documented(_OAUTH_REGISTRY, params)

        seen: list[dict[str, Any]] = []
        page = 1
        while True:
            resp = oauth_configs_api.get_registry(page=page, limit=10)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert_response_strictly_matches_openapi_operation(body, _OAUTH_REGISTRY)
            assert body["pagination"]["page"] == page and body["pagination"]["limit"] == 10
            seen += body["connectors"]
            if not body["pagination"]["hasNext"]:
                break
            page += 1
        assert page > 1, "the registry did not paginate"
        assert len(seen) == body["pagination"]["totalCount"]
        assert len({c["type"] for c in seen}) == len(seen)
        assert {"Jira", "Confluence"} <= {c["type"] for c in seen}
        assert all(c["authType"] == "OAUTH" for c in seen)

    def test_registry_search_filters_the_entries(
        self, oauth_configs_api: ConnectorOAuthConfigsClient
    ) -> None:
        everything = oauth_configs_api.get_registry().json()["pagination"]["totalCount"]
        resp = oauth_configs_api.get_registry(search="jira")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_strictly_matches_openapi_operation(body, _OAUTH_REGISTRY)
        assert body["pagination"]["search"] == "jira"
        assert "Jira" in {c["type"] for c in body["connectors"]}
        assert body["pagination"]["totalCount"] < everything

        nothing = oauth_configs_api.get_registry(search="connector-spec-it-no-such-type").json()
        assert nothing["connectors"] == []
        assert_response_strictly_matches_openapi_operation(nothing, _OAUTH_REGISTRY)

    @pytest.mark.parametrize("params", [{"limit": 201}, {"limit": 0}, {"page": 0}])
    def test_registry_with_bad_paging_is_400(
        self, oauth_configs_api: ConnectorOAuthConfigsClient, params: dict[str, int]
    ) -> None:
        resp = oauth_configs_api.get_registry(**params)
        assert resp.status_code == 400, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _OAUTH_REGISTRY, 400)

    @pytest.mark.parametrize(
        "connector_type",
        ["Jira", "Confluence", "Calendar", "Dropbox", "Salesforce", "OneDrive"],
    )
    def test_registry_entry_matches_spec(
        self, oauth_configs_api: ConnectorOAuthConfigsClient, connector_type: str
    ) -> None:
        """Entries differ: some carry ``additionalParams``, a few ``tokenAccessType``."""
        resp = oauth_configs_api.get_registry_entry(connector_type)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["connector"]["type"] == connector_type
        assert_response_strictly_matches_openapi_operation(body, _OAUTH_REGISTRY_ENTRY)

    def test_registry_entry_lists_the_fields_to_register_an_app(
        self, oauth_configs_api: ConnectorOAuthConfigsClient
    ) -> None:
        entry = oauth_configs_api.get_registry_entry("Jira").json()["connector"]
        assert {f["name"] for f in entry["authFields"]} >= {"clientId", "clientSecret"}
        assert entry["redirectUri"] == "connectors/oauth/callback/Jira"

    def test_unknown_registry_entry_is_404(
        self, oauth_configs_api: ConnectorOAuthConfigsClient
    ) -> None:
        resp = oauth_configs_api.get_registry_entry("connector-spec-it-no-such-type")
        assert resp.status_code == 404, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _OAUTH_REGISTRY_ENTRY, 404)

    @pytest.mark.parametrize("operation_id", [_OAUTH_REGISTRY, _OAUTH_REGISTRY_ENTRY])
    def test_registry_unauthenticated_is_401(
        self, oauth_configs_api: ConnectorOAuthConfigsClient, operation_id: str
    ) -> None:
        if operation_id == _OAUTH_REGISTRY:
            resp = oauth_configs_api.get_registry(auth=False)
        else:
            resp = oauth_configs_api.get_registry_entry("Jira", auth=False)
        assert resp.status_code == 401, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), operation_id, 401)

    @pytest.mark.parametrize("operation_id", [_OAUTH_REGISTRY, _OAUTH_REGISTRY_ENTRY])
    def test_registry_documents_and_needs_exactly_connector_read(
        self,
        oauth_configs_api: ConnectorOAuthConfigsClient,
        scoped_token: Callable[[Sequence[str]], str],
        operation_id: str,
    ) -> None:
        assert operation_oauth2_scopes(operation_id) == ["connector:read"]

        def call(scope: str) -> Any:
            headers = {"Authorization": f"Bearer {scoped_token([scope])}"}
            if operation_id == _OAUTH_REGISTRY:
                return oauth_configs_api.get_registry(auth=False, headers=headers)
            return oauth_configs_api.get_registry_entry("Jira", auth=False, headers=headers)

        refused = call("kb:read")
        assert refused.status_code == 403, refused.text
        assert_error_response_matches_openapi_operation(refused.json(), operation_id, 403)
        assert call("connector:read").status_code == 200


@pytest.mark.integration
class TestOAuthSignIn:
    def test_authorize_matches_spec(
        self,
        connectors_api: ConnectorsClient,
        config_client: ConfigClient,
        jira_oauth_connector: Any,
    ) -> None:
        resp = connectors_api.get_oauth_authorization_url(jira_oauth_connector.connector_id)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        sent = jira_oauth_connector.request_body
        query = parse_qs(urlparse(body["authorizationUrl"]).query)
        assert query.get("client_id") == [sent["config"]["auth"]["clientId"]], query
        # The configured public frontend URL wins over the baseUrl the connector was
        # created with; the baseUrl is used only on a server with none configured.
        frontend = config_client.get_frontend_public_url()
        assert frontend.status_code == 200, frontend.text
        redirect_base = (frontend.json().get("url") or sent["baseUrl"]).rstrip("/")
        assert query.get("redirect_uri") == [
            f"{redirect_base}/connectors/oauth/callback/Jira"
        ], query
        # Jira takes no token_access_type; an unset one must be left out, not sent as "None".
        assert "token_access_type" not in query, query
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
