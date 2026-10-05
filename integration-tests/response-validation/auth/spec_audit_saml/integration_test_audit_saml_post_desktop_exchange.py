"""Strict OpenAPI audit of POST /api/v1/saml/desktop/exchange.

The route is public: the handoff code plus PKCE verifier are the credential.
A code is only issued by a real IdP round trip, so the 200 path is out of reach.
"""

from __future__ import annotations

from typing import Any

import pytest
from saml_audit_support import (
    CODE_VERIFIER,
    DESKTOP_EXCHANGE_ROUTE,
    MALFORMED_CODE_VERIFIER,
    MALFORMED_HANDOFF_CODE,
    MISSING_HANDOFF_CODE,
    SamlClient,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = DESKTOP_EXCHANGE_ROUTE
INVALID_CODE_MESSAGE = "Invalid or expired sign-in code"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            {"code": MISSING_HANDOFF_CODE, "codeVerifier": CODE_VERIFIER},
            id="never_issued_code",
        ),
        pytest.param(
            {"code": MALFORMED_HANDOFF_CODE, "codeVerifier": CODE_VERIFIER},
            id="malformed_code",
        ),
        pytest.param(
            {"code": MISSING_HANDOFF_CODE, "codeVerifier": MALFORMED_CODE_VERIFIER},
            id="malformed_verifier",
        ),
    ],
)
def test_exchange_rejects_unredeemable_code(
    saml_client: SamlClient, body: dict[str, Any]
) -> None:
    resp = saml_client.desktop_exchange(body)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == "HTTP_UNAUTHORIZED"
    assert error["message"] == INVALID_CODE_MESSAGE
    assert "accessToken" not in resp.text


def test_exchange_without_code_verifier_fails_validation(
    saml_client: SamlClient,
) -> None:
    resp = saml_client.desktop_exchange({"code": MISSING_HANDOFF_CODE})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.xfail(
    strict=True,
    reason="API bug: an unparseable JSON body is answered 500 INTERNAL_ERROR instead of 400",
)
def test_exchange_with_unparseable_json_body(saml_client: SamlClient) -> None:
    # express.json() raises a SyntaxError (status 400) that the error middleware
    # does not recognise, so the client gets a 500 for its own malformed body.
    resp = saml_client.desktop_exchange(
        data='{"code": ', headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
