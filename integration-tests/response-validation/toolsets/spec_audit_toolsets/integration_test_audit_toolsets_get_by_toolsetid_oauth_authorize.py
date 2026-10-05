"""Strict OpenAPI audit of GET /api/v1/toolsets/:toolsetId/oauth/authorize.

Legacy route: Node validates and proxies, but Python has no handler for it (only
/instances/:instanceId/oauth/authorize), so every authenticated call ends in the
proxied 404. There is no success path to test.
"""

from __future__ import annotations

import pytest
from strict_openapi import assert_strict_openapi_response
from toolsets_audit_support import (
    MISSING_TOOLSET_ID,
    UNSAFE_PATH_ID,
    SeedToolsetInstance,
    ToolsetsClient,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/:toolsetId/oauth/authorize"


def _authorize_path(toolset_id: str) -> str:
    return f"/{toolset_id}/oauth/authorize"


def test_authorize_for_an_unknown_toolset_id_is_not_found(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.get(
        _authorize_path(MISSING_TOOLSET_ID), params={"base_url": "https://spec-audit.invalid"}
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_authorize_is_not_found_even_for_a_real_oauth_instance(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    # The id that works on /instances/:instanceId/oauth/authorize; here nothing serves it.
    instance = seed_toolset_instance(oauth=True)
    resp = toolsets_client.get(_authorize_path(instance["_id"]))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_authorize_rejects_a_repeated_base_url(toolsets_client: ToolsetsClient) -> None:
    # Express parses a repeated key into an array, which the zod string refuses.
    resp = toolsets_client.get(
        _authorize_path(MISSING_TOOLSET_ID),
        params=[("base_url", "https://a.invalid"), ("base_url", "https://b.invalid")],
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_authorize_rejects_a_call_without_a_token(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get(_authorize_path(MISSING_TOOLSET_ID), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_authorize_refuses_an_unsafe_toolset_id_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.get(_authorize_path(UNSAFE_PATH_ID), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
