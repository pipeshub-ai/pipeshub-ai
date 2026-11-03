"""Response-validation ITs for the connector registry: the calls an SDK user makes
before creating a connector.

- GET  /api/v1/connectors/registry
- GET  /api/v1/connectors/registry/{connectorType}/schema
- POST /api/v1/connectors/registry/{connectorType}/test-connection

Each success response must be fully described by pipeshub-openapi.yaml: a
generated SDK reads only documented fields, so an undocumented one is lost.
"""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.clients.connectors_client import ConnectorsClient  # noqa: E402
from helper.pipeshub_client import PipeshubClient  # noqa: E402
from openapi_schema_validator import (  # noqa: E402
    assert_error_response_matches_openapi_operation,
    assert_query_params_documented,
    assert_request_body_strictly_matches_openapi_operation,
    assert_response_strictly_matches_openapi_operation,
    operation_parameter_default,
)

_REGISTRY = "getConnectorRegistry"
_SCHEMA = "getConnectorSchema"
_TEST_CONNECTION = "testConnectorConnection"


@pytest.mark.integration
class TestConnectorRegistry:
    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.connectors = ConnectorsClient(pipeshub_client)

    @pytest.mark.parametrize("scope", ["team", "personal"])
    def test_list_registry_matches_spec(self, scope: str) -> None:
        params = {"scope": scope, "page": 1, "limit": 200}
        assert_query_params_documented(_REGISTRY, params)

        resp = self.connectors.get_registry(**params)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("connectors"), f"registry listed no {scope} connector types: {body}"

        assert_response_strictly_matches_openapi_operation(body, _REGISTRY)

    def test_list_registry_scope_defaults_to_team(self) -> None:
        """Leaving ``scope`` out lists only team connector types, so an SDK
        caller who wants personal ones has to know the default."""
        default = self.connectors.get_registry(limit=200)
        team = self.connectors.get_registry(scope="team", limit=200)
        assert default.status_code == 200, default.text
        assert team.status_code == 200, team.text
        assert [c.get("type") for c in default.json()["connectors"]] == [
            c.get("type") for c in team.json()["connectors"]
        ]

        documented = operation_parameter_default(_REGISTRY, "scope")
        assert documented == "team", (
            f"the server defaults scope to 'team' but the spec documents default={documented!r}"
        )

    @pytest.mark.parametrize(
        "connector_type",
        [
            "RSS",  # auth type NONE
            "Jira",  # OAUTH or API_TOKEN
            "PostgreSQL",  # BASIC_AUTH or CONNECTION_STRING
        ],
    )
    def test_get_schema_matches_spec(self, connector_type: str) -> None:
        resp = self.connectors.get_registry_schema(connector_type)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("schema"), f"no schema for {connector_type}: {body}"

        assert_response_strictly_matches_openapi_operation(body, _SCHEMA)

    def test_get_schema_unknown_type_is_404(self) -> None:
        resp = self.connectors.get_registry_schema(f"NoSuchConnector{uuid4().hex[:8]}")
        assert resp.status_code == 404, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _SCHEMA, 404)

    def test_list_registry_unauthenticated_is_401(self) -> None:
        resp = self.connectors.get_registry(auth=False)
        assert resp.status_code == 401, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _REGISTRY, 401)


@pytest.mark.integration
class TestConnectorTestConnection:
    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.connectors = ConnectorsClient(pipeshub_client)

    def test_failed_check_is_200_with_success_false(self) -> None:
        """A check that cannot connect is still a 200; ``success`` carries the result."""
        auth = {
            "host": "127.0.0.1",
            "port": 1,
            "database": "connector_spec_it",
            "username": "connector_spec_it",
            "password": f"pw-{uuid4().hex}",
        }
        resp = self.connectors.test_connection("PostgreSQL", auth)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is False, body
        assert body["message"], body

        assert_request_body_strictly_matches_openapi_operation({"auth": auth}, _TEST_CONNECTION)
        assert_response_strictly_matches_openapi_operation(body, _TEST_CONNECTION)

    def test_type_without_connection_check_is_400(self) -> None:
        resp = self.connectors.test_connection("RSS", {})
        assert resp.status_code == 400, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _TEST_CONNECTION, 400)

    def test_unknown_type_is_404(self) -> None:
        resp = self.connectors.test_connection(f"NoSuchConnector{uuid4().hex[:8]}", {})
        assert resp.status_code == 404, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _TEST_CONNECTION, 404)

    def test_unauthenticated_is_401(self) -> None:
        resp = self.connectors.test_connection("PostgreSQL", {}, auth=False)
        assert resp.status_code == 401, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _TEST_CONNECTION, 401)
