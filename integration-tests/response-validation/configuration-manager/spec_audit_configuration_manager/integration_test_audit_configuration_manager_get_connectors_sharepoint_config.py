"""Strict OpenAPI audit of GET /api/v1/configurationManager/connectors/sharepoint/config."""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/connectors/sharepoint/config"
PATH = "/connectors/sharepoint/config"


def test_admin_reads_the_stored_sharepoint_credentials(config_client: ConfigClient) -> None:
    resp = config_client.get(PATH)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    # The decrypted stored object as-is, or {} when the org never saved one.
    assert isinstance(resp.json(), dict), resp.text[:500]


def test_admin_read_ignores_unknown_query_params(config_client: ConfigClient) -> None:
    # No validator on this route, so stray params are not a 400.
    resp = config_client.get(PATH, params={"specAudit": "bogus"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == config_client.get(PATH).json()


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param(INVALID_BEARER_HEADERS, id="invalid-token"),
    ],
)
def test_rejects_unauthenticated_calls(
    config_client: ConfigClient, headers: dict[str, str]
) -> None:
    resp = config_client.get(PATH, auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_is_refused_by_the_admin_gate(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", PATH)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
