"""Every /api/v1/oauth2 route answers 500 to a malformed JSON body.

The JSON body parser runs before the router and its failure is reported as an
internal error, so even GET routes that read no body, and routes that would
otherwise redirect or ask for a token, fail this way first.
"""

from __future__ import annotations

import pytest
from oauth2_audit_support import (
    JSON_HEADERS,
    MALFORMED_JSON_BODY,
    OAUTH2_BASE,
    OAuth2Client,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

OPERATIONS = [
    ("GET", "/authorize"),
    ("POST", "/authorize"),
    ("POST", "/token"),
    ("POST", "/revoke"),
    ("POST", "/introspect"),
    ("GET", "/userinfo"),
    ("POST", "/register"),
    ("POST", "/device_authorization"),
    ("POST", "/device/verify"),
    ("POST", "/device/consent"),
]


@pytest.mark.parametrize(
    ("method", "sub_path"), OPERATIONS, ids=[f"{m} {p}" for m, p in OPERATIONS]
)
def test_malformed_json_body_is_an_internal_error(
    oauth2_client: OAuth2Client, method: str, sub_path: str
) -> None:
    resp = oauth2_client._client.request(
        method,
        f"{OAUTH2_BASE}{sub_path}",
        auth=False,
        data=MALFORMED_JSON_BODY,
        headers=JSON_HEADERS,
        allow_redirects=False,
    )

    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, f"{OAUTH2_BASE}{sub_path}")
