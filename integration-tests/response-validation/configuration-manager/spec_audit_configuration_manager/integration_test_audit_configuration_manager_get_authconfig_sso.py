"""Strict OpenAPI audit of GET /api/v1/configurationManager/authConfig/sso.

Chain: authenticate -> requireScopes(config:read) -> userAdminCheck -> getSsoAuthConfig.
No validator: the handler reads nothing from the request.
"""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    KV_AUTH_SSO,
    SSO_CERTIFICATE_BODY,
    SSO_DERIVED_FIELDS,
    SSO_VALID_BODY,
    GuardSavedConfig,
    forget_stored_config,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/authConfig/sso"
PATH = "/authConfig/sso"


def test_admin_reads_only_the_sp_entity_id_when_nothing_is_saved(
    config_client: ConfigClient, guard_saved_config: GuardSavedConfig
) -> None:
    guard_saved_config(PATH, KV_AUTH_SSO, SSO_DERIVED_FIELDS)
    forget_stored_config(KV_AUTH_SSO)

    resp = config_client.get(PATH)

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert list(body) == ["spEntityId"], body
    assert isinstance(body["spEntityId"], str) and body["spEntityId"]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_admin_reads_the_saved_config_with_certificate_and_sp_entity_id(
    config_client: ConfigClient, guard_saved_config: GuardSavedConfig
) -> None:
    before = guard_saved_config(PATH, KV_AUTH_SSO, SSO_DERIVED_FIELDS)
    saved = config_client.post(PATH, json=SSO_VALID_BODY)
    assert saved.status_code == 200, saved.text[:500]

    resp = config_client.get(PATH)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == {
        **SSO_VALID_BODY,
        "certificate": SSO_CERTIFICATE_BODY,
        # From the server's environment: the same whether or not a config is saved.
        "spEntityId": before["spEntityId"],
    }
    assert_strict_openapi_exchange(resp, ROUTE)


def test_admin_read_does_not_read_the_query_string(config_client: ConfigClient) -> None:
    plain = config_client.get(PATH)
    assert plain.status_code == 200, plain.text[:500]

    with outside_request_contract("the handler never reads the query string"):
        resp = config_client.get(PATH, params={"specAudit": "bogus"})

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == plain.json()
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no-token", "invalid-token"],
)
def test_unauthenticated_is_unauthorized(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.get(PATH, auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", PATH)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
