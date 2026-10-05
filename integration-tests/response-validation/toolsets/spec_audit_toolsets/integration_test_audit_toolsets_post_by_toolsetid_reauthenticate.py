"""Strict OpenAPI audit of POST /api/v1/toolsets/:toolsetId/reauthenticate.

Legacy route: Node still registers and proxies it, but Python has no handler for
``/toolsets/{id}/reauthenticate``, so there is no success path to test.
"""

from __future__ import annotations

import pytest
from toolsets_audit_support import (
    MISSING_TOOLSET_ID,
    UNSAFE_PATH_ID,
    SeedToolsetInstance,
    ToolsetsClient,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/:toolsetId/reauthenticate"


def test_reauthenticate_unknown_toolset_is_proxied_not_found(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.post(f"/{MISSING_TOOLSET_ID}/reauthenticate")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_reauthenticate_existing_instance_id_is_still_not_found(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    instance = seed_toolset_instance()

    resp = toolsets_client.post(f"/{instance['_id']}/reauthenticate")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    still_there = toolsets_client.get_instance(instance["_id"])
    assert still_there.status_code == 200, still_there.text[:500]


@pytest.mark.xfail(
    strict=True,
    reason="API bug: POST /toolsets/instances/reauthenticate answers 500 "
    "(the id is pasted into a Python path without a POST handler; its 405 is mapped to a 500)",
)
def test_reauthenticate_reserved_segment_is_not_found(
    toolsets_client: ToolsetsClient,
) -> None:
    # "instances" makes the proxied path /instances/reauthenticate, which Python matches
    # for GET/PUT/DELETE only; its 405 is not a status Node's error mapper keeps. Nothing
    # can be reauthenticated under any id, so the answer every other id gets is a 404.
    resp = toolsets_client.post("/instances/reauthenticate")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_reauthenticate_without_token_is_unauthorized(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.post(f"/{MISSING_TOOLSET_ID}/reauthenticate", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_reauthenticate_unsafe_toolset_id_is_refused_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.post(f"/{UNSAFE_PATH_ID}/reauthenticate", auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
