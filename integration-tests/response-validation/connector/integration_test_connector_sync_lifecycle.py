"""Response-validation ITs for the calls around connecting a source and syncing it.

- GET    /api/v1/connectors/network/egress-ips
- GET    /api/v1/connectors
- GET    /api/v1/connectors/{connectorId}/stats
- POST   /api/v1/connectors/{connectorId}/resync
- POST   /api/v1/connectors/{connectorId}/sync/stop
- DELETE /api/v1/connectors/{connectorId}

Each success response must be fully described by pipeshub-openapi.yaml, down to
the fields inside nested objects: an object the spec leaves as a bare
``type: object`` reaches an SDK caller as an untyped dict.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.clients.connectors_client import ConnectorsClient  # noqa: E402
from openapi_schema_validator import (  # noqa: E402
    assert_error_response_matches_openapi_operation,
    assert_operation_requires_authentication,
    assert_query_params_documented,
    assert_request_body_strictly_matches_openapi_operation,
    assert_response_strictly_matches_openapi_operation,
    operation_parameter_accepts,
    operation_parameter_default,
    request_body_violations,
)

_EGRESS_IPS = "getConnectorEgressIps"
_LIST = "listConnectorInstances"
_STATS = "getConnectorStats"
_RESYNC = "resyncConnector"
_STOP_SYNC = "stopConnectorSync"
_DELETE = "deleteConnectorInstance"


def _unknown_connector_id() -> str:
    return str(uuid4())


@pytest.mark.integration
class TestEgressIps:
    def test_egress_ips_match_spec(self, connectors_api: ConnectorsClient) -> None:
        resp = connectors_api.get_egress_ips()
        assert resp.status_code == 200, resp.text
        assert_response_strictly_matches_openapi_operation(resp.json(), _EGRESS_IPS, opaque=True)

    def test_egress_ips_unauthenticated_is_401(self, connectors_api: ConnectorsClient) -> None:
        resp = connectors_api.get_egress_ips(auth=False)
        assert resp.status_code == 401, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _EGRESS_IPS, 401)


@pytest.mark.integration
class TestListConnectorInstances:
    @pytest.mark.parametrize("scope", ["team", "personal"])
    def test_list_matches_spec(
        self, connectors_api: ConnectorsClient, rss_connector: Any, scope: str
    ) -> None:
        params = {"scope": scope, "page": 1, "limit": 200}
        assert_query_params_documented(_LIST, params)
        resp = connectors_api.list_instances(**params)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if scope == "team":
            ids = {c.get("_key") for c in body["connectors"]}
            assert rss_connector.connector_id in ids, "the team RSS connector is not listed"
        assert_response_strictly_matches_openapi_operation(body, _LIST, opaque=True)

    def test_list_filters_match_spec(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        instance = connectors_api.get_instance(rss_connector.connector_id).json()["connector"]
        params = {
            "scope": "team",
            "search": rss_connector.request_body["instanceName"],
            "isActive": str(bool(instance["isActive"])).lower(),
            "isAuthenticated": str(bool(instance["isAuthenticated"])).lower(),
            "connectorType": "RSS",
        }
        assert_query_params_documented(_LIST, params)
        resp = connectors_api.list_instances(params=params)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [c.get("_key") for c in body["connectors"]] == [rss_connector.connector_id]
        assert_response_strictly_matches_openapi_operation(body, _LIST, opaque=True)

    def test_list_scope_defaults_to_team(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        default = connectors_api.list_instances(limit=200)
        team = connectors_api.list_instances(scope="team", limit=200)
        assert default.status_code == 200, default.text
        assert team.status_code == 200, team.text
        assert [c.get("_key") for c in default.json()["connectors"]] == [
            c.get("_key") for c in team.json()["connectors"]
        ]
        assert operation_parameter_default(_LIST, "scope") == "team"

    @pytest.mark.parametrize(
        ("param", "value"),
        [("scope", "everyone"), ("isActive", "maybe"), ("limit", "201"), ("page", "0")],
    )
    def test_list_refused_params_are_documented(
        self, connectors_api: ConnectorsClient, param: str, value: str
    ) -> None:
        resp = connectors_api.list_instances(params={param: value})
        assert resp.status_code == 400, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _LIST, 400)
        typed: Any = int(value) if param in ("limit", "page") else value
        assert not operation_parameter_accepts(_LIST, param, typed), (
            f"the server refuses {param}={value!r} but the spec accepts it"
        )

    def test_list_unauthenticated_is_401(self, connectors_api: ConnectorsClient) -> None:
        resp = connectors_api.list_instances(auth=False)
        assert resp.status_code == 401, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _LIST, 401)


@pytest.mark.integration
class TestConnectorStats:
    def test_stats_of_a_synced_connector_match_spec(
        self, connectors_api: ConnectorsClient, active_rss_connector: Any
    ) -> None:
        resp = connectors_api.get_stats(active_rss_connector.connector_id)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["data"]["stats"]["total"] >= 1, f"the first sync stored no record: {body}"
        assert body["data"]["byRecordType"], body
        assert_response_strictly_matches_openapi_operation(body, _STATS, opaque=True)

    def test_stats_of_a_connector_never_synced_match_spec(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        resp = connectors_api.get_stats(rss_connector.connector_id)
        assert resp.status_code == 200, resp.text
        assert_response_strictly_matches_openapi_operation(resp.json(), _STATS, opaque=True)

    def test_stats_of_unknown_connector_is_404(self, connectors_api: ConnectorsClient) -> None:
        resp = connectors_api.get_stats(_unknown_connector_id())
        assert resp.status_code == 404, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _STATS, 404)

    def test_stats_unauthenticated_is_401(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        resp = connectors_api.get_stats(rss_connector.connector_id, auth=False)
        assert resp.status_code == 401, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _STATS, 401)


@pytest.mark.integration
class TestResync:
    def test_resync_matches_spec(
        self,
        connectors_api: ConnectorsClient,
        active_rss_connector: Any,
        wait_until_idle: Callable[[str], None],
    ) -> None:
        body = {"connectorName": "RSS", "fullSync": False}
        assert_request_body_strictly_matches_openapi_operation(body, _RESYNC)
        try:
            resp = connectors_api.resync(active_rss_connector.connector_id, "RSS", full_sync=False)
            assert resp.status_code == 200, resp.text
            assert_response_strictly_matches_openapi_operation(resp.json(), _RESYNC, opaque=True)
        finally:
            wait_until_idle(active_rss_connector.connector_id)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_resync_while_a_sync_runs_is_409(
        self,
        connectors_api: ConnectorsClient,
        active_rss_connector: Any,
        lock_connector: Callable[[str], Awaitable[None]],
    ) -> None:
        """A resync sent while a sync holds the connector is refused."""
        await lock_connector(active_rss_connector.connector_id)
        resp = connectors_api.resync(active_rss_connector.connector_id, "RSS")
        assert resp.status_code == 409, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _RESYNC, 409)

    def test_resync_of_inactive_connector_is_400(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        """Sync is off, so there is nothing to resync."""
        resp = connectors_api.resync(rss_connector.connector_id, "RSS")
        assert resp.status_code == 400, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _RESYNC, 400)

    def test_resync_of_unknown_connector_is_400(self, connectors_api: ConnectorsClient) -> None:
        resp = connectors_api.resync(_unknown_connector_id(), "RSS")
        assert resp.status_code == 400, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _RESYNC, 400)

    def test_resync_without_connector_name_is_400(
        self, connectors_api: ConnectorsClient, active_rss_connector: Any
    ) -> None:
        resp = connectors_api.resync(active_rss_connector.connector_id, "RSS", json={})
        assert resp.status_code == 400, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _RESYNC, 400)
        assert request_body_violations({}, _RESYNC), "the spec accepts a body without connectorName"

    def test_resync_with_empty_connector_name_is_400(
        self, connectors_api: ConnectorsClient, active_rss_connector: Any
    ) -> None:
        body = {"connectorName": ""}
        resp = connectors_api.resync(active_rss_connector.connector_id, "", json=body)
        assert resp.status_code == 400, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _RESYNC, 400)
        assert request_body_violations(body, _RESYNC), "the spec accepts an empty connectorName"

    def test_resync_unauthenticated_is_401(
        self, connectors_api: ConnectorsClient, active_rss_connector: Any
    ) -> None:
        resp = connectors_api.resync(active_rss_connector.connector_id, "RSS", auth=False)
        assert resp.status_code == 401, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _RESYNC, 401)


@pytest.mark.integration
class TestStopSync:
    def test_stop_with_nothing_running_matches_spec(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        resp = connectors_api.stop_sync(rss_connector.connector_id)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["stopped"] is False, body
        assert_response_strictly_matches_openapi_operation(body, _STOP_SYNC, opaque=True)

    def test_stop_of_unknown_connector_is_404(self, connectors_api: ConnectorsClient) -> None:
        resp = connectors_api.stop_sync(_unknown_connector_id())
        assert resp.status_code == 404, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _STOP_SYNC, 404)

    def test_stop_unauthenticated_is_401(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        resp = connectors_api.stop_sync(rss_connector.connector_id, auth=False)
        assert resp.status_code == 401, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _STOP_SYNC, 401)


@pytest.mark.integration
class TestDeleteConnector:
    def test_delete_matches_spec(self, connectors_api: ConnectorsClient, rss_feed_url: str) -> None:
        created = connectors_api.create_instance(
            {
                "connectorType": "RSS",
                "instanceName": f"connector-spec-it-rss-delete-{uuid4().hex[:8]}",
                "scope": "team",
                "config": {"sync": {"feed_urls": rss_feed_url}},
            }
        )
        assert created.status_code == 200, created.text
        connector_id = created.json()["connector"]["connectorId"]

        resp = connectors_api.delete_instance(connector_id)
        assert resp.status_code == 202, resp.text
        assert_response_strictly_matches_openapi_operation(
            resp.json(), _DELETE, status_code="202", opaque=True
        )

        again = connectors_api.delete_instance(connector_id)
        assert again.status_code == 404, again.text
        assert_error_response_matches_openapi_operation(again.json(), _DELETE, 404)

    def test_delete_unknown_connector_is_404(self, connectors_api: ConnectorsClient) -> None:
        resp = connectors_api.delete_instance(_unknown_connector_id())
        assert resp.status_code == 404, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _DELETE, 404)

    def test_delete_unauthenticated_is_401(
        self, connectors_api: ConnectorsClient, rss_connector: Any
    ) -> None:
        resp = connectors_api.delete_instance(rss_connector.connector_id, auth=False)
        assert resp.status_code == 401, resp.text
        assert_error_response_matches_openapi_operation(resp.json(), _DELETE, 401)


@pytest.mark.integration
@pytest.mark.parametrize(
    "operation_id", [_EGRESS_IPS, _LIST, _STATS, _RESYNC, _STOP_SYNC, _DELETE]
)
def test_operation_requires_authentication(operation_id: str) -> None:
    assert_operation_requires_authentication(operation_id)
