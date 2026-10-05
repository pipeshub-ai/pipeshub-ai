"""Strict OpenAPI audit of GET /api/v1/toolsets/instances."""

from __future__ import annotations

import pytest
from toolsets_audit_support import SeedToolsetInstance, ToolsetsClient, request_as
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/instances"


def test_admin_lists_instances_with_registry_metadata(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance()

    resp = toolsets_client.list_instances(search=seeded["instanceName"].upper(), limit=200)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["status"] == "success"
    by_id = {instance["_id"]: instance for instance in body["instances"]}
    assert seeded["_id"] in by_id
    listed = by_id[seeded["_id"]]
    assert listed["instanceName"] == seeded["instanceName"]
    for enriched_key in ("displayName", "description", "iconPath", "toolCount"):
        assert enriched_key in listed
    pagination = body["pagination"]
    assert (pagination["page"], pagination["limit"]) == (1, 200)
    assert pagination["total"] == len(body["instances"])
    assert pagination["hasPrev"] is False


def test_member_lists_instances_without_inline_credentials(
    second_user: SecondUser, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance()

    resp = request_as(
        second_user, "GET", "/instances", params={"search": seeded["instanceName"]}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert seeded["_id"] in {instance["_id"] for instance in body["instances"]}
    assert all("auth" not in instance for instance in body["instances"])
    # Python's default page size; Node adds none of its own.
    assert body["pagination"]["limit"] == 50


@pytest.mark.parametrize(
    "params",
    [{"limit": 201}, {"page": "abc"}],
    ids=["limit-above-max", "page-not-a-number"],
)
def test_invalid_paging_is_refused_by_python(
    toolsets_client: ToolsetsClient, params: dict[str, int | str]
) -> None:
    # No zod schema on this route: the query reaches FastAPI, whose 422 Node relays as is.
    resp = toolsets_client.get("/instances", params=params)
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_instances_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.list_instances(auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
