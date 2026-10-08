"""Strict OpenAPI audit of POST /api/v1/toolsets/:toolsetId/reauthenticate.

Legacy route: Node pastes the id into POST `/api/v1/toolsets/{id}/reauthenticate` on
the connector service, which has no such route, so an ordinary id ends in the relayed
routing 404. For the ids `instances`, `oauth-configs` and `agents` the URL matches a
Python route without a POST handler; its 405 comes back as a 500.
"""

from __future__ import annotations

import pytest
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)
from toolsets_audit_support import (
    MISSING_TOOLSET_ID,
    NO_BACKEND_ROUTE,
    RESERVED_SEGMENTS,
    UNSAFE_PATH_ID,
    SeedToolsetInstance,
    ToolsetsClient,
    assert_backend_failure,
    assert_not_found,
    request_as,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/:toolsetId/reauthenticate"


def _path(toolset_id: str) -> str:
    return f"/{toolset_id}/reauthenticate"


def test_reauthenticate_unknown_toolset_is_proxied_not_found(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.post(_path(MISSING_TOOLSET_ID))
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_reauthenticate_existing_instance_id_is_still_not_found(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    instance = seed_toolset_instance(oauth=True)

    resp = toolsets_client.post(_path(instance["_id"]))
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_exchange(resp, ROUTE)

    # The id is live on the instance route, so the 404 above is the route's, not the id's.
    live = toolsets_client.post(f"/instances/{instance['_id']}/reauthenticate")
    assert live.status_code == 200, live.text[:500]


def test_reauthenticate_as_member_is_not_found(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", _path(MISSING_TOOLSET_ID))
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("toolset_id", RESERVED_SEGMENTS)
def test_reauthenticate_for_a_reserved_id_is_an_internal_error(
    toolsets_client: ToolsetsClient, toolset_id: str
) -> None:
    # API bug: the pasted URL (/instances/reauthenticate, ...) matches a Python route
    # that has no POST handler. FastAPI answers 405, and Node turns every backend status
    # it does not list into a 500.
    resp = toolsets_client.post(_path(toolset_id))
    assert_backend_failure(resp)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_reauthenticate_reads_neither_query_nor_body(toolsets_client: ToolsetsClient) -> None:
    with outside_request_contract("the route reads no query parameter and forwards no body"):
        resp = toolsets_client.post(
            _path(MISSING_TOOLSET_ID), params={"force": "true"}, json={"force": True}
        )
    assert_not_found(resp, NO_BACKEND_ROUTE)
    assert_strict_openapi_response(resp, ROUTE)


def test_reauthenticate_without_token_is_unauthorized(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.post(_path(MISSING_TOOLSET_ID), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_reauthenticate_unsafe_toolset_id_is_refused_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.post(_path(UNSAFE_PATH_ID), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
