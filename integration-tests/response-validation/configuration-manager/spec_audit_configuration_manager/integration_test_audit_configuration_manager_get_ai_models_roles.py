"""Strict OpenAPI audit of GET /api/v1/configurationManager/ai-models/roles.

Chain: authenticate -> requireScopes(config:read) -> userAdminCheck -> getModelRoles.
"""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/ai-models/roles"
PATH = "/ai-models/roles"


def test_admin_reads_the_role_map(config_client: ConfigClient) -> None:
    resp = config_client.get(PATH)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert set(body) == {"status", "modelRoles"}, body
    assert body["status"] == "success"
    for role, assignment in body["modelRoles"].items():
        assert set(assignment) == {"modelType", "modelKey"}, (role, assignment)


@pytest.mark.parametrize("headers", [None, INVALID_BEARER_HEADERS], ids=["no-token", "invalid-token"])
def test_without_valid_token_is_unauthorized(config_client: ConfigClient, headers: dict[str, str] | None) -> None:
    resp = config_client.get(PATH, auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", PATH)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_config_read_scope_is_forbidden(config_client: ConfigClient, narrow_scope_headers: dict[str, str]) -> None:
    resp = config_client.get("/ai-models/roles", auth=False, headers=narrow_scope_headers)

    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
