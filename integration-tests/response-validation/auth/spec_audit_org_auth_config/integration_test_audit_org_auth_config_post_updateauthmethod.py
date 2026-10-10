"""Strict OpenAPI audit of POST /api/v1/orgAuthConfig/updateAuthMethod.

Every policy written here keeps password in the first and only step, so other suites
signing in meanwhile are not affected, and the original policy is put back after each test.
"""

from __future__ import annotations

from typing import Any, Callable, Iterator

import pytest
from helper.pipeshub_client import PipeshubClient
from org_auth_config_audit_support import (
    ACCOUNT_NOT_FOUND,
    ADMIN_ACCESS_REQUIRED,
    BAD_REQUEST,
    FORBIDDEN,
    INTERNAL_ERROR,
    MISSING_ORG_ID,
    NO_AUTHORIZATION_HEADER,
    NO_TOKEN_AFTER_SCHEME,
    NOT_FOUND,
    SAML_IN_MULTI_STEP_POLICY,
    UPDATE_AUTH_METHOD_ROUTE,
    UPDATED_MESSAGE,
    VALIDATION_ERROR,
    OrgAuthConfigClient,
    OTHER_SIGNING_KEY,
    mint_access_token,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from user_account_audit_support import SignInPolicy, bearer, steps

pytestmark = pytest.mark.spec_audit

ROUTE = UPDATE_AUTH_METHOD_ROUTE
PASSWORD_ONLY = steps(["password"])


@pytest.fixture
def restored_policy(sign_in_policy: SignInPolicy) -> Iterator[None]:
    original = sign_in_policy.current()
    try:
        yield
    finally:
        sign_in_policy.replace(original)


def _assert_error(resp: Any, status: int, code: str, message: str | None = None) -> None:
    assert resp.status_code == status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == code
    if message is not None:
        assert error["message"] == message


@pytest.mark.parametrize(
    "policy",
    [
        pytest.param(PASSWORD_ONLY, id="password_only"),
        pytest.param(steps(["password", "otp", "google", "microsoft", "azureAd", "oauth"]), id="many_methods"),
        pytest.param(steps(["password", "github"]), id="github"),
        pytest.param(steps(["samlSso", "password"]), id="saml_in_a_one_step_policy"),
        # order is only checked to be a number; steps run in array order.
        pytest.param([{"order": 7, "allowedMethods": [{"type": "password"}]}], id="order_above_three"),
        pytest.param([{"order": -2, "allowedMethods": [{"type": "password"}]}], id="negative_order"),
        pytest.param([{"order": 1.5, "allowedMethods": [{"type": "password"}]}], id="fractional_order"),
    ],
)
def test_admin_replaces_the_policy_and_it_is_echoed(
    org_auth_config_client: OrgAuthConfigClient,
    admin_headers: dict[str, str],
    sign_in_policy: SignInPolicy,
    restored_policy: None,
    policy: list[dict[str, Any]],
) -> None:
    resp = org_auth_config_client.update_auth_method({"authMethod": policy}, headers=admin_headers)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"message": UPDATED_MESSAGE, "authMethod": policy}
    assert sign_in_policy.current() == policy


def test_unknown_fields_are_dropped_and_the_query_is_ignored(
    org_auth_config_client: OrgAuthConfigClient,
    admin_headers: dict[str, str],
    sign_in_policy: SignInPolicy,
    restored_policy: None,
) -> None:
    body = {
        "authMethod": [
            {"order": 1, "allowedMethods": [{"type": "password", "specAuditUnknown": 1}], "specAuditUnknown": 2}
        ],
        "specAuditUnknown": 3,
    }
    with outside_request_contract("the validator drops unknown fields at every level and the query"):
        resp = org_auth_config_client.update_auth_method(
            body, headers=admin_headers, params={"specAuditUnknown": "1"}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["authMethod"] == PASSWORD_ONLY
    assert sign_in_policy.current() == PASSWORD_ONLY


def _step(order: Any, *methods: str) -> dict[str, Any]:
    return {"order": order, "allowedMethods": [{"type": m} for m in methods]}


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        pytest.param({}, ["body.authMethod"], id="no_auth_method"),
        pytest.param(None, ["body.authMethod"], id="no_body"),
        pytest.param({"authMethod": []}, ["body.authMethod"], id="no_steps"),
        pytest.param(
            {"authMethod": [_step(1, "password"), _step(2, "otp"), _step(3, "google"), _step(4, "microsoft")]},
            ["body.authMethod"],
            id="four_steps",
        ),
        pytest.param({"authMethod": [_step(1)]}, ["body.authMethod.0.allowedMethods"], id="step_without_methods"),
        pytest.param(
            {"authMethod": [_step(1, "spec-audit-no-such-method")]},
            ["body.authMethod.0.allowedMethods.0.type"],
            id="unknown_method",
        ),
        pytest.param({"authMethod": [_step("1", "password")]}, ["body.authMethod.0.order"], id="order_as_string"),
        pytest.param(
            {"authMethod": [{"allowedMethods": [{"type": "password"}]}]},
            ["body.authMethod.0.order"],
            id="no_order",
        ),
        pytest.param(
            {"authMethod": [_step(1, "password", "password")]},
            ["body.authMethod.0.allowedMethods", "body.authMethod"],
            id="same_method_twice_in_a_step",
        ),
        pytest.param(
            {"authMethod": [_step(1, "password"), _step(2, "samlSso")]},
            ["body.authMethod"],
            id="saml_in_a_two_step_policy",
        ),
        pytest.param({"authMethod": {"order": 1}}, ["body.authMethod"], id="steps_not_an_array"),
        pytest.param(["not", "an", "object"], ["body"], id="array_body"),
    ],
)
def test_update_refuses_what_the_validator_refuses(
    org_auth_config_client: OrgAuthConfigClient,
    admin_headers: dict[str, str],
    body: Any,
    fields: list[str],
) -> None:
    resp = org_auth_config_client.update_auth_method(body, headers=admin_headers)
    _assert_error(resp, 400, VALIDATION_ERROR)
    assert [e["field"] for e in resp.json()["error"]["metadata"]["errors"]] == fields


def test_saml_in_a_two_step_policy_names_the_reason(
    org_auth_config_client: OrgAuthConfigClient, admin_headers: dict[str, str]
) -> None:
    resp = org_auth_config_client.update_auth_method(
        {"authMethod": [_step(1, "password"), _step(2, "samlSso")]}, headers=admin_headers
    )
    _assert_error(resp, 400, VALIDATION_ERROR, SAML_IN_MULTI_STEP_POLICY)


@pytest.mark.parametrize(
    ("steps_sent", "message"),
    [
        pytest.param([_step(1, "password"), _step(1, "otp")], "Duplicate order found: 1", id="same_order_twice"),
        pytest.param(
            [_step(1, "password"), _step(2, "password")],
            'Authentication method "password" is repeated across multiple steps',
            id="same_method_in_two_steps",
        ),
    ],
)
def test_rules_across_steps_are_refused(
    org_auth_config_client: OrgAuthConfigClient,
    admin_headers: dict[str, str],
    steps_sent: list[dict[str, Any]],
    message: str,
) -> None:
    # JSON Schema cannot compare one array item with another, so the spec states these
    # two rules in prose and the request is outside what its schema can refuse.
    with outside_request_contract("uniqueness across steps is documented but not expressible in the schema"):
        resp = org_auth_config_client.update_auth_method({"authMethod": steps_sent}, headers=admin_headers)
        _assert_error(resp, 400, VALIDATION_ERROR, message)
    assert [e["field"] for e in resp.json()["error"]["metadata"]["errors"]] == ["body.authMethod"]


@pytest.mark.parametrize(
    ("authorization", "message"),
    [
        pytest.param(None, NO_AUTHORIZATION_HEADER, id="no_authorization_header"),
        pytest.param("Bearer", NO_TOKEN_AFTER_SCHEME, id="scheme_without_a_token"),
    ],
)
def test_request_without_a_bearer_token_is_bad_request(
    org_auth_config_client: OrgAuthConfigClient, authorization: str | None, message: str
) -> None:
    headers = {} if authorization is None else {"Authorization": authorization}
    _assert_error(
        org_auth_config_client.update_auth_method({"authMethod": PASSWORD_ONLY}, headers=headers),
        400,
        BAD_REQUEST,
        message,
    )


def test_token_is_checked_before_the_body(org_auth_config_client: OrgAuthConfigClient) -> None:
    with outside_request_contract("an invalid body, to show the token is checked first"):
        resp = org_auth_config_client.update_auth_method({"authMethod": []})
        _assert_error(resp, 400, BAD_REQUEST, NO_AUTHORIZATION_HEADER)


@pytest.mark.parametrize(
    "token_kind", ["not_a_jwt", "signed_with_another_key", "expired"]
)
def test_token_that_does_not_verify_is_an_internal_error(
    org_auth_config_client: OrgAuthConfigClient,
    access_token: Callable[..., str],
    admin_claims: dict[str, str],
    token_kind: str,
) -> None:
    token = {
        "not_a_jwt": lambda: "spec-audit-not-a-jwt",
        "signed_with_another_key": lambda: mint_access_token(OTHER_SIGNING_KEY, **admin_claims),
        "expired": lambda: access_token(ttl_seconds=-60, **admin_claims),
    }[token_kind]()
    _assert_error(
        org_auth_config_client.update_auth_method({"authMethod": PASSWORD_ONLY}, headers=bearer(token)),
        500,
        INTERNAL_ERROR,
    )


@pytest.mark.parametrize("caller", ["member", "oauth_app", "admin_of_another_org"])
def test_caller_who_is_not_an_admin_of_the_org_is_forbidden(
    org_auth_config_client: OrgAuthConfigClient,
    pipeshub_client: PipeshubClient,
    member_headers: dict[str, str],
    access_token: Callable[..., str],
    admin_claims: dict[str, str],
    caller: str,
) -> None:
    body = {"authMethod": PASSWORD_ONLY}
    if caller == "oauth_app":
        resp = pipeshub_client.request("POST", ROUTE, json=body)
    else:
        headers = {
            "member": lambda: member_headers,
            "admin_of_another_org": lambda: bearer(
                access_token(userId=admin_claims["userId"], orgId=MISSING_ORG_ID)
            ),
        }[caller]()
        resp = org_auth_config_client.update_auth_method(body, headers=headers)
    _assert_error(resp, 403, FORBIDDEN, ADMIN_ACCESS_REQUIRED)


@pytest.mark.parametrize("missing", ["userId", "orgId"])
def test_token_without_user_or_org_is_not_found(
    org_auth_config_client: OrgAuthConfigClient,
    access_token: Callable[..., str],
    admin_claims: dict[str, str],
    missing: str,
) -> None:
    claims = {k: v for k, v in admin_claims.items() if k != missing}
    _assert_error(
        org_auth_config_client.update_auth_method(
            {"authMethod": PASSWORD_ONLY}, headers=bearer(access_token(**claims))
        ),
        404,
        NOT_FOUND,
        ACCOUNT_NOT_FOUND,
    )
