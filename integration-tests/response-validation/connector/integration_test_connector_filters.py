"""Response-validation ITs for the connector lists and the filter and config routes.

- GET  /api/v1/connectors/active, /inactive, /agents/active
- GET  /api/v1/connectors/{connectorId}/filters
- POST /api/v1/connectors/{connectorId}/filters
- PUT  /api/v1/connectors/{connectorId}/config

Each success response must be fully described by pipeshub-openapi.yaml, and each
refusal the server gives (400, 401, 403, 404) must be a documented status.
Scopes are the ones the code behind each route checks.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator, Sequence
from uuid import uuid4

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from conftest import CreatedConnector, _create_connector, _delete_connector  # noqa: E402
from helper.clients.connectors_client import ConnectorsClient  # noqa: E402
from openapi_schema_validator import (  # noqa: E402
    assert_error_response_matches_openapi_operation,
    assert_request_body_strictly_matches_openapi_operation,
    assert_response_strictly_matches_openapi_operation,
    operation_oauth2_scopes,
    request_body_violations,
    validate_instance_with_ref,
)

_LIST_OPS = {
    "listActiveConnectors": "list_active",
    "listInactiveConnectors": "list_inactive",
    "listActiveAgentConnectors": "list_active_agents",
}
_GET_FILTERS = "getConnectorFilters"
_SAVE_FILTERS = "saveConnectorFilters"
_UPDATE_CONFIG = "updateConnectorConfig"


@pytest.fixture(scope="module")
def jira_token_connector(connectors_api: ConnectorsClient) -> Iterator[CreatedConnector]:
    """Team Jira connector with API-token auth; nothing contacts Jira when it is saved."""
    created = _create_connector(
        connectors_api,
        {
            "connectorType": "Jira",
            "instanceName": f"connector-spec-it-jira-token-{uuid4().hex[:8]}",
            "scope": "team",
            "authType": "API_TOKEN",
            "config": {
                "auth": {
                    "baseUrl": "http://127.0.0.1:1",
                    "email": "connector-spec-it@example.com",
                    "apiToken": f"token-{uuid4().hex}",
                }
            },
        },
    )
    try:
        yield created
    finally:
        _delete_connector(connectors_api, created.connector_id)


@pytest.mark.integration
class TestConnectorLists:
    @pytest.mark.parametrize("operation_id", list(_LIST_OPS))
    def test_list_matches_spec(
        self, connectors_api: ConnectorsClient, rss_connector: Any, operation_id: str
    ) -> None:
        resp = getattr(connectors_api, _LIST_OPS[operation_id])()
        assert resp.status_code == 200, resp.text
        assert_response_strictly_matches_openapi_operation(resp.json(), operation_id)

    def test_instance_without_updater_is_null(self) -> None:
        """The personal KB connector has never been updated, so the lists carry
        ``updatedBy: null`` (GET /connectors/active returned it); the schema all of
        them share must accept it."""
        validate_instance_with_ref(
            {"_key": "kb", "type": "KB", "scope": "personal", "createdBy": "u", "updatedBy": None},
            "#/components/schemas/ConnectorInstance",
        )

    def test_active_list_unauthenticated_is_401(self, connectors_api: ConnectorsClient) -> None:
        resp = connectors_api.list_active(auth=False)
        assert resp.status_code == 401, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), "listActiveConnectors", 401)


@pytest.mark.integration
class TestGetFilters:
    def test_connector_without_credentials_auth_is_400(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        """RSS has auth type NONE, which has no filter options to load."""
        resp = connectors_api.get_filters(rss_connector.connector_id)
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["message"] == "Unsupported authentication type: NONE"
        assert_error_response_matches_openapi_operation(resp.json(), _GET_FILTERS, 400)

    def test_basic_auth_connector_is_400(
        self, connectors_api: ConnectorsClient, postgres_connector: Any
    ) -> None:
        resp = connectors_api.get_filters(postgres_connector.connector_id)
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["message"] == "Unsupported authentication type: BASIC_AUTH"
        assert_error_response_matches_openapi_operation(resp.json(), _GET_FILTERS, 400)

    def test_oauth_connector_not_signed_in_is_400(
        self, connectors_api: ConnectorsClient, jira_oauth_connector: Any
    ) -> None:
        resp = connectors_api.get_filters(jira_oauth_connector.connector_id)
        assert resp.status_code == 400, resp.text
        assert "OAuth credentials not found" in resp.json()["error"]["message"]
        assert_error_response_matches_openapi_operation(resp.json(), _GET_FILTERS, 400)

    def test_api_token_connector_matches_spec(
        self, connectors_api: ConnectorsClient, jira_token_connector: Any
    ) -> None:
        resp = connectors_api.get_filters(jira_token_connector.connector_id)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        # No connector type lists filters whose choices come from the service.
        assert body["filterOptions"] == {}
        assert_response_strictly_matches_openapi_operation(body, _GET_FILTERS)

    def test_unknown_connector_is_404(self, connectors_api: ConnectorsClient) -> None:
        resp = connectors_api.get_filters(str(uuid4()))
        assert resp.status_code == 404, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _GET_FILTERS, 404)

    def test_unauthenticated_is_401(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        resp = connectors_api.get_filters(rss_connector.connector_id, auth=False)
        assert resp.status_code == 401, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _GET_FILTERS, 401)

    def test_documented_scopes_are_exactly_connector_read(self) -> None:
        assert operation_oauth2_scopes(_GET_FILTERS) == ["connector:read"]

    def test_token_without_documented_scope_is_403(
        self,
        connectors_api: ConnectorsClient,
        jira_token_connector: Any,
        scoped_token: Callable[[Sequence[str]], str],
    ) -> None:
        token = scoped_token(["connector:write"])
        resp = connectors_api.get_filters(
            jira_token_connector.connector_id,
            auth=False,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _GET_FILTERS, 403)

    def test_documented_scope_is_enough(
        self,
        connectors_api: ConnectorsClient,
        jira_token_connector: Any,
        scoped_token: Callable[[Sequence[str]], str],
    ) -> None:
        token = scoped_token(operation_oauth2_scopes(_GET_FILTERS))
        resp = connectors_api.get_filters(
            jira_token_connector.connector_id,
            auth=False,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text


@pytest.mark.integration
class TestSaveFilters:
    def test_save_matches_spec(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        body = {"filters": {"fileTypes": ["pdf", "docx"]}}
        assert_request_body_strictly_matches_openapi_operation(body, _SAVE_FILTERS)
        resp = connectors_api.save_filters(rss_connector.connector_id, body)
        assert resp.status_code == 200, resp.text
        assert_response_strictly_matches_openapi_operation(resp.json(), _SAVE_FILTERS)

    def test_empty_filters_is_400(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        body: dict[str, Any] = {"filters": {}}
        resp = connectors_api.save_filters(rss_connector.connector_id, body)
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["message"] == "No filter selections provided"
        assert_error_response_matches_openapi_operation(resp.json(), _SAVE_FILTERS, 400)
        assert request_body_violations(body, _SAVE_FILTERS), "the spec accepts empty filters"

    def test_missing_filters_is_400(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        resp = connectors_api.save_filters(rss_connector.connector_id, {})
        assert resp.status_code == 400, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _SAVE_FILTERS, 400)
        assert request_body_violations({}, _SAVE_FILTERS), "the spec accepts a body without filters"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_locked_connector_is_409(
        self,
        connectors_api: ConnectorsClient,
        rss_connector: Any,
        lock_connector: Callable[[str], Awaitable[None]],
    ) -> None:
        await lock_connector(rss_connector.connector_id)
        resp = connectors_api.save_filters(rss_connector.connector_id, {"filters": {"a": 1}})
        assert resp.status_code == 409, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _SAVE_FILTERS, 409)

    def test_unknown_connector_is_404(self, connectors_api: ConnectorsClient) -> None:
        resp = connectors_api.save_filters(str(uuid4()), {"filters": {"a": 1}})
        assert resp.status_code == 404, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _SAVE_FILTERS, 404)

    def test_unauthenticated_is_401(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        resp = connectors_api.save_filters(
            rss_connector.connector_id, {"filters": {"a": 1}}, auth=False
        )
        assert resp.status_code == 401, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _SAVE_FILTERS, 401)

    def test_documented_scopes_are_exactly_connector_write(self) -> None:
        """Node checks ``connector:write`` (connectors.routes.ts) and so does Python."""
        assert operation_oauth2_scopes(_SAVE_FILTERS) == ["connector:write"]

    def test_token_without_documented_scope_is_403(
        self,
        connectors_api: ConnectorsClient,
        rss_connector: Any,
        scoped_token: Callable[[Sequence[str]], str],
    ) -> None:
        token = scoped_token(["connector:read"])
        resp = connectors_api.save_filters(
            rss_connector.connector_id,
            {"filters": {"a": 1}},
            auth=False,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _SAVE_FILTERS, 403)

    def test_documented_scope_is_enough(
        self,
        connectors_api: ConnectorsClient,
        rss_connector: Any,
        scoped_token: Callable[[Sequence[str]], str],
    ) -> None:
        token = scoped_token(operation_oauth2_scopes(_SAVE_FILTERS))
        resp = connectors_api.save_filters(
            rss_connector.connector_id,
            {"filters": {"a": 1}},
            auth=False,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text


@pytest.mark.integration
class TestUpdateConfig:
    def test_request_and_response_match_spec(
        self, connectors_api: ConnectorsClient, rss_connector: Any, rss_feed_url: str
    ) -> None:
        body = {
            "sync": {
                "selectedStrategy": "MANUAL",
                "feed_urls": rss_feed_url,
                "max_articles_per_feed": 2,
            },
            "filters": {},
        }
        assert_request_body_strictly_matches_openapi_operation(body, _UPDATE_CONFIG)
        resp = connectors_api.update_config(rss_connector.connector_id, body)
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["message"] == "Configuration saved successfully."
        assert out["config"]["sync"]["max_articles_per_feed"] == 2
        assert set(out["config"]) == {"auth", "sync", "filters"}
        assert_response_strictly_matches_openapi_operation(out, _UPDATE_CONFIG)

    def test_auth_section_response_matches_spec(
        self, connectors_api: ConnectorsClient, postgres_connector: Any
    ) -> None:
        auth = postgres_connector.request_body["config"]["auth"]
        body = {"auth": {**auth, "port": 4}}
        assert_request_body_strictly_matches_openapi_operation(body, _UPDATE_CONFIG)
        resp = connectors_api.update_config(postgres_connector.connector_id, body)
        assert resp.status_code == 200, resp.text
        assert auth["password"] not in resp.text
        assert_response_strictly_matches_openapi_operation(resp.json(), _UPDATE_CONFIG)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_locked_connector_is_409(
        self,
        connectors_api: ConnectorsClient,
        rss_connector: Any,
        lock_connector: Callable[[str], Awaitable[None]],
    ) -> None:
        await lock_connector(rss_connector.connector_id)
        resp = connectors_api.update_config(rss_connector.connector_id, {"filters": {}})
        assert resp.status_code == 409, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _UPDATE_CONFIG, 409)

    def test_unknown_connector_is_404(self, connectors_api: ConnectorsClient) -> None:
        resp = connectors_api.update_config(str(uuid4()), {"sync": {"selectedStrategy": "MANUAL"}})
        assert resp.status_code == 404, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _UPDATE_CONFIG, 404)

    def test_unauthenticated_is_401(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        resp = connectors_api.update_config(
            rss_connector.connector_id, {"sync": {"selectedStrategy": "MANUAL"}}, auth=False
        )
        assert resp.status_code == 401, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _UPDATE_CONFIG, 401)

    def test_documented_scopes(self) -> None:
        """Write is checked by Node and Python; read is for the schedule reconcile that a
        saved ``sync`` starts with the caller's token (as for config/filters-sync)."""
        assert sorted(operation_oauth2_scopes(_UPDATE_CONFIG)) == [
            "connector:read",
            "connector:write",
        ]

    def test_token_without_write_is_403(
        self,
        connectors_api: ConnectorsClient,
        rss_connector: Any,
        scoped_token: Callable[[Sequence[str]], str],
    ) -> None:
        token = scoped_token(["connector:read"])
        resp = connectors_api.update_config(
            rss_connector.connector_id,
            {"sync": {"selectedStrategy": "MANUAL"}},
            auth=False,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _UPDATE_CONFIG, 403)

    def test_documented_scopes_are_enough_and_cover_the_reconcile_read(
        self,
        connectors_api: ConnectorsClient,
        rss_connector: Any,
        scoped_token: Callable[[Sequence[str]], str],
    ) -> None:
        documented = operation_oauth2_scopes(_UPDATE_CONFIG)
        headers = {"auth": False, "headers": {"Authorization": f"Bearer {scoped_token(documented)}"}}
        resp = connectors_api.update_config(
            rss_connector.connector_id, {"sync": {"selectedStrategy": "MANUAL"}}, **headers
        )
        assert resp.status_code == 200, resp.text
        # The schedule reconcile that saving `sync` starts reads the config with this token.
        read = connectors_api.get_config(rss_connector.connector_id, **headers)
        assert read.status_code == 200, read.text


@pytest.mark.integration
class TestActiveConnector:
    def test_update_config_of_active_connector_is_400(
        self,
        connectors_api: ConnectorsClient,
        active_rss_connector: Any,
        wait_until_idle: Callable[[str], None],
    ) -> None:
        """Sync is on, so the settings cannot change until it is turned off."""
        wait_until_idle(active_rss_connector.connector_id)
        resp = connectors_api.update_config(
            active_rss_connector.connector_id, {"sync": {"selectedStrategy": "MANUAL"}}
        )
        assert resp.status_code == 400, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _UPDATE_CONFIG, 400)
