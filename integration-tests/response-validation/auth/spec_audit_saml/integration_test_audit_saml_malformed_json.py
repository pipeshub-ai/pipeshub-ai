"""Every /api/v1/saml route answers 500 to a malformed JSON body.

The JSON body parser runs before the router and its failure is reported as an
internal error, so the callback answers JSON here instead of its usual redirect.
"""

from __future__ import annotations

import pytest
from saml_audit_support import (
    DESKTOP_EXCHANGE_ROUTE,
    JSON_HEADERS,
    MALFORMED_JSON_BODY,
    SIGN_IN_CALLBACK_ROUTE,
    SIGN_IN_ROUTE,
    UPDATE_APP_CONFIG_ROUTE,
    SamlClient,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit


@pytest.mark.parametrize(
    ("method", "sub_path", "route"),
    [
        ("GET", "/signIn", SIGN_IN_ROUTE),
        ("POST", "/signIn/callback", SIGN_IN_CALLBACK_ROUTE),
        ("POST", "/desktop/exchange", DESKTOP_EXCHANGE_ROUTE),
        ("POST", "/updateAppConfig", UPDATE_APP_CONFIG_ROUTE),
    ],
    ids=["sign_in", "sign_in_callback", "desktop_exchange", "update_app_config"],
)
def test_malformed_json_body_is_an_internal_error(
    saml_client: SamlClient, method: str, sub_path: str, route: str
) -> None:
    send = saml_client.get if method == "GET" else saml_client.post
    resp = send(
        sub_path,
        auth=False,
        data=MALFORMED_JSON_BODY,
        headers=JSON_HEADERS,
        allow_redirects=False,
    )
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
