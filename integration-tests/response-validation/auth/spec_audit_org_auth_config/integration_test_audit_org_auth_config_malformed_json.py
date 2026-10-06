"""Every /api/v1/orgAuthConfig route answers 500 to a malformed JSON body.

The JSON body parser runs before the router and its failure is reported as an
internal error, before the token is looked at.
"""

from __future__ import annotations

import pytest
from org_auth_config_audit_support import (
    AUTH_METHODS_ROUTE,
    JSON_HEADERS,
    MALFORMED_JSON_BODY,
    SET_UP_ROUTE,
    UPDATE_AUTH_METHOD_ROUTE,
    OrgAuthConfigClient,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit


@pytest.mark.parametrize(
    ("method", "sub_path", "route"),
    [
        ("GET", "/authMethods", AUTH_METHODS_ROUTE),
        ("POST", "", SET_UP_ROUTE),
        ("POST", "/updateAuthMethod", UPDATE_AUTH_METHOD_ROUTE),
    ],
    ids=["auth_methods", "set_up", "update_auth_method"],
)
def test_malformed_json_body_is_an_internal_error(
    org_auth_config_client: OrgAuthConfigClient, method: str, sub_path: str, route: str
) -> None:
    send = org_auth_config_client.get if method == "GET" else org_auth_config_client.post
    resp = send(sub_path, auth=False, data=MALFORMED_JSON_BODY, headers=JSON_HEADERS)
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
