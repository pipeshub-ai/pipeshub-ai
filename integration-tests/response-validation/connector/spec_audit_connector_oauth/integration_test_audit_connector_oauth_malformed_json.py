"""Every /api/v1/oauth route answers 500 to a malformed JSON body.

The JSON body parser runs before the router and its failure is reported as an
internal error, so the request reaches neither the path guard nor the token check.
"""

from __future__ import annotations

import pytest
from connector_oauth_audit_support import (
    JSON_HEADERS,
    MALFORMED_JSON_BODY,
    MISSING_CONFIG_ID,
    OAUTH_BASE,
    SEED_CONNECTOR_TYPE,
    ConnectorOAuthClient,
    error_code,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

_TYPE = f"/{SEED_CONNECTOR_TYPE}"
_CONFIG = f"/{SEED_CONNECTOR_TYPE}/{MISSING_CONFIG_ID}"


@pytest.mark.parametrize(
    ("method", "sub_path", "route"),
    [
        ("GET", "/registry", f"{OAUTH_BASE}/registry"),
        ("GET", f"/registry{_TYPE}", f"{OAUTH_BASE}/registry/:connectorType"),
        ("GET", "", OAUTH_BASE),
        ("GET", _TYPE, f"{OAUTH_BASE}/:connectorType"),
        ("POST", _TYPE, f"{OAUTH_BASE}/:connectorType"),
        ("GET", _CONFIG, f"{OAUTH_BASE}/:connectorType/:configId"),
        ("PUT", _CONFIG, f"{OAUTH_BASE}/:connectorType/:configId"),
        ("DELETE", _CONFIG, f"{OAUTH_BASE}/:connectorType/:configId"),
    ],
    ids=[
        "get_registry",
        "get_registry_entry",
        "list_all",
        "list_for_type",
        "create",
        "get_config",
        "update_config",
        "delete_config",
    ],
)
def test_malformed_json_body_is_an_internal_error_before_the_token_check(
    connector_oauth_client: ConnectorOAuthClient, method: str, sub_path: str, route: str
) -> None:
    # API bug: a body that does not parse is a caller mistake, yet it answers 500.
    resp = connector_oauth_client.send(
        method,
        sub_path,
        auth=False,
        data=MALFORMED_JSON_BODY,
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 500, resp.text[:500]
    assert error_code(resp) == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
