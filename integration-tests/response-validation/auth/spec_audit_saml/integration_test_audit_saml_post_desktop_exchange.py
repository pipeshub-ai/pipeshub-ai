"""Strict OpenAPI audit of POST /api/v1/saml/desktop/exchange.

The route is public: the handoff code plus PKCE verifier are the credential.
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
    OTHER_CODE_VERIFIER,
    DummyIdp,
    SamlClient,
    desktop_relay_state,
    redirect_target,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from user_account_audit_support import DisposableMember, UserAccountAuditClient

pytestmark = pytest.mark.spec_audit

ROUTE = DESKTOP_EXCHANGE_ROUTE
INVALID_CODE_MESSAGE = "Invalid or expired sign-in code"


@pytest.fixture
def handoff_code(
    saml_client: SamlClient,
    dummy_idp: DummyIdp,
    service_provider: str,
    saml_member: DisposableMember,
    saml_sign_in_allowed: None,
) -> str:
    """A fresh code from a desktop sign-in, issued for CODE_CHALLENGE."""
    resp = saml_client.sign_in_callback(
        {
            "SAMLResponse": dummy_idp.response(saml_member.email, service_provider),
            "RelayState": desktop_relay_state(),
        }
    )
    assert resp.status_code == 302, resp.text[:500]
    code = redirect_target(resp)[1].get("code", [""])[0]
    assert code, resp.headers.get("Location")
    return code


def _assert_refused(resp: Any) -> None:
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == "HTTP_UNAUTHORIZED"
    assert error["message"] == INVALID_CODE_MESSAGE
    assert "accessToken" not in resp.text


def test_exchange_returns_the_tokens_once(
    saml_client: SamlClient,
    user_account_audit_client: UserAccountAuditClient,
    saml_member: DisposableMember,
    handoff_code: str,
) -> None:
    body = {"code": handoff_code, "codeVerifier": CODE_VERIFIER}

    resp = saml_client.desktop_exchange(body)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    tokens = resp.json()
    assert set(tokens) == {"accessToken", "refreshToken"}

    # The refresh route is what proves the token is a real one for this account.
    refreshed = user_account_audit_client.refresh_token(token=tokens["refreshToken"])
    assert refreshed.status_code == 200, refreshed.text[:500]
    assert refreshed.json()["user"]["_id"] == saml_member.user_id

    _assert_refused(saml_client.desktop_exchange(body))


def test_wrong_verifier_burns_the_code(saml_client: SamlClient, handoff_code: str) -> None:
    _assert_refused(
        saml_client.desktop_exchange({"code": handoff_code, "codeVerifier": OTHER_CODE_VERIFIER})
    )
    _assert_refused(
        saml_client.desktop_exchange({"code": handoff_code, "codeVerifier": CODE_VERIFIER})
    )


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
    _assert_refused(saml_client.desktop_exchange(body))


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        pytest.param({"code": MISSING_HANDOFF_CODE}, ["body.codeVerifier"], id="no_verifier"),
        pytest.param({"codeVerifier": CODE_VERIFIER}, ["body.code"], id="no_code"),
        pytest.param(None, ["body.code", "body.codeVerifier"], id="no_body"),
        pytest.param(
            {"code": "", "codeVerifier": ""}, ["body.code", "body.codeVerifier"], id="empty_strings"
        ),
        pytest.param(
            {"code": 1, "codeVerifier": 2}, ["body.code", "body.codeVerifier"], id="numbers"
        ),
        pytest.param(["not", "an", "object"], ["body"], id="array_body"),
    ],
)
def test_exchange_refuses_what_the_validator_refuses(
    saml_client: SamlClient, body: Any, fields: list[str]
) -> None:
    resp = saml_client.desktop_exchange(body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in error["metadata"]["errors"]] == fields


def test_exchange_ignores_unknown_fields_and_the_query_string(saml_client: SamlClient) -> None:
    with outside_request_contract("the validator drops body fields and query keys it does not know"):
        resp = saml_client.desktop_exchange(
            {"code": MISSING_HANDOFF_CODE, "codeVerifier": CODE_VERIFIER, "specAuditUnknown": 1},
            params={"specAuditUnknown": "1"},
        )
        # Past the validator: refused for the code, not for the extra field.
        _assert_refused(resp)
