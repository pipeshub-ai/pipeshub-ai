"""Strict OpenAPI audit of GET /.well-known/openid-configuration."""

from __future__ import annotations

import pytest
from oidc_discovery_audit_support import (
    OPENID_CONFIGURATION_ROUTE,
    UNKNOWN_QUERY,
    DiscoveryClient,
    assert_server_metadata,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit


def test_openid_configuration_describes_the_server(discovery_client: DiscoveryClient) -> None:
    resp = discovery_client.fetch(OPENID_CONFIGURATION_ROUTE)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, OPENID_CONFIGURATION_ROUTE)
    assert_server_metadata(resp.json())


def test_unknown_query_parameters_are_ignored(discovery_client: DiscoveryClient) -> None:
    with outside_request_contract("the route takes no parameters; this shows it ignores them"):
        resp = discovery_client.fetch(OPENID_CONFIGURATION_ROUTE, params=UNKNOWN_QUERY)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, OPENID_CONFIGURATION_ROUTE)
    assert_server_metadata(resp.json())
