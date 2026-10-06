"""Strict OpenAPI audit of GET /api/v1/toolsets/configured.

API bug: the route answers 500 to every caller that gets past the token and scope
checks. Python's get_configured_toolsets calls get_my_toolsets() as a plain function,
so page, limit, includeRegistry, toolsetType and authStatus keep their fastapi.Query()
default objects; the type filter then matches nothing and `(page - 1) * limit` raises
TypeError, whatever the store holds. Node forwards no query string either.
"""

from __future__ import annotations

import pytest
from strict_openapi import assert_strict_openapi_exchange
from toolsets_audit_support import (
    SeedToolsetInstance,
    ToolsetsClient,
    assert_backend_failure,
    request_as,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/configured"


@pytest.mark.parametrize(
    "params",
    [{}, {"page": 1, "limit": 5, "includeRegistry": "false"}],
    ids=["no_query", "paging_query_not_forwarded"],
)
def test_configured_is_an_internal_error_for_admin(
    toolsets_client: ToolsetsClient, params: dict[str, int | str]
) -> None:
    resp = toolsets_client.get("/configured", params=params)
    assert_backend_failure(resp)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_configured_is_an_internal_error_with_a_configured_instance(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seed_toolset_instance()

    resp = toolsets_client.get("/configured")
    assert_backend_failure(resp)
    assert_strict_openapi_exchange(resp, ROUTE)

    # The list this route was written to return is intact on the route it delegates to.
    working = toolsets_client.get("/my-toolsets")
    assert working.status_code == 200, working.text[:500]


def test_configured_is_an_internal_error_for_member(second_user: SecondUser) -> None:
    # No admin gate in Node or Python: the member reaches the same handler.
    resp = request_as(second_user, "GET", "/configured")
    assert_backend_failure(resp)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_configured_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get("/configured", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
