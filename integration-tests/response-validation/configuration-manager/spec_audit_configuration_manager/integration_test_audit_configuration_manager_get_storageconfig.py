"""Strict OpenAPI audit of GET /api/v1/configurationManager/storageConfig.

Chain: authenticate -> requireScopes(config:read) -> userAdminCheck -> getStorageConfig.
No validator: the handler reads nothing from the request.
"""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/storageConfig"
PATH = "/storageConfig"


def test_admin_gets_an_empty_object_not_the_configuration(config_client: ConfigClient) -> None:
    # API bug: the handler returns the stored fields only to a caller without a user id,
    # which is the service token of /internal/storageConfig. Every caller of this route
    # has one, so the answer is {} for every storage type.
    resp = config_client.get(PATH)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == {}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_admin_read_does_not_read_the_query_string(config_client: ConfigClient) -> None:
    with outside_request_contract("the handler never reads the query string"):
        resp = config_client.get(PATH, params={"specAudit": "bogus", "storageType": "s3"})

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == {}
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
