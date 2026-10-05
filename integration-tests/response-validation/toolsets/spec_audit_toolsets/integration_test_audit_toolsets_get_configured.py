"""Strict OpenAPI audit of GET /api/v1/toolsets/configured."""

from __future__ import annotations

import pytest
from toolsets_audit_support import ToolsetsClient, request_as
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/configured"

# Python's get_configured_toolsets calls get_my_toolsets() as a plain function, so page,
# limit, includeRegistry, toolsetType and authStatus keep their fastapi.Query(...) default
# objects. The type filter then matches no instance, the truthy includeRegistry skips the
# empty-list early return, and `(page - 1) * limit` raises TypeError: a 500 for every
# authenticated caller, whatever the store holds. Node forwards no query string either.


@pytest.mark.parametrize(
    "params",
    [{}, {"page": 1, "limit": 5}],
    ids=["no_query", "paging_query_not_forwarded"],
)
def test_configured_fails_for_admin(
    toolsets_client: ToolsetsClient, params: dict[str, int]
) -> None:
    resp = toolsets_client.get("/configured", params=params)
    assert resp.status_code == 500, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_configured_fails_for_member(second_user: SecondUser) -> None:
    # No admin gate in Node or Python: the member reaches the same broken handler.
    resp = request_as(second_user, "GET", "/configured")
    assert resp.status_code == 500, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_configured_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get("/configured", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
