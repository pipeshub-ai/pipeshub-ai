"""Strict OpenAPI audit of GET /api/v1/toolsets/instances."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)
from toolsets_audit_support import (
    SeedToolsetInstance,
    ToolsetsClient,
    error_of,
    request_as,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/instances"
INLINE_AUTH = {"apiToken": "spec-audit-inline-token", "extra": {"nested": True}}


def test_admin_lists_instances_with_registry_metadata(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance()

    resp = toolsets_client.list_instances(search=seeded["instanceName"].upper(), limit=200)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["status"] == "success"
    assert [instance["_id"] for instance in body["instances"]] == [seeded["_id"]]
    listed = body["instances"][0]
    assert listed == {
        **seeded,
        "displayName": listed["displayName"],
        "description": listed["description"],
        "iconPath": listed["iconPath"],
        "toolCount": listed["toolCount"],
    }
    assert listed["toolCount"] > 0
    assert body["pagination"] == {
        "page": 1,
        "limit": 200,
        "total": 1,
        "totalPages": 1,
        "hasNext": False,
        "hasPrev": False,
    }


def test_inline_credentials_are_masked_for_admin_and_hidden_from_member(
    toolsets_client: ToolsetsClient,
    second_user: SecondUser,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    seeded = seed_toolset_instance(authConfig=INLINE_AUTH)
    search = {"search": seeded["instanceName"]}

    resp = toolsets_client.get("/instances", params=search)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    # Only client secrets and nested objects are masked; the inline API token is not.
    assert resp.json()["instances"][0]["auth"] == {
        "apiToken": INLINE_AUTH["apiToken"],
        "extra": "\u2022" * 8,
    }

    as_member = request_as(second_user, "GET", "/instances", params=search)
    assert as_member.status_code == 200, as_member.text[:500]
    assert_strict_openapi_exchange(as_member, ROUTE)
    body = as_member.json()
    assert [instance["_id"] for instance in body["instances"]] == [seeded["_id"]]
    assert "auth" not in body["instances"][0]
    # Python's default page size; Node adds none of its own.
    assert body["pagination"]["limit"] == 50


def test_search_matches_the_toolset_type_and_pages_slice_the_result(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    prefix = f"spec-audit-page-{uuid.uuid4().hex[:8]}"
    seeded = [seed_toolset_instance(instanceName=f"{prefix}-{index}") for index in range(2)]

    resp = toolsets_client.list_instances(search=prefix, limit=1, page=2)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert [instance["_id"] for instance in body["instances"]] == [seeded[1]["_id"]]
    assert body["pagination"] == {
        "page": 2,
        "limit": 1,
        "total": 2,
        "totalPages": 2,
        "hasNext": False,
        "hasPrev": True,
    }

    by_type = toolsets_client.list_instances(search=seeded[0]["toolsetType"], limit=200)
    assert by_type.status_code == 200, by_type.text[:500]
    assert_strict_openapi_exchange(by_type, ROUTE)
    assert {record["_id"] for record in seeded} <= {instance["_id"] for instance in by_type.json()["instances"]}


@pytest.mark.parametrize("name", ["page", "limit", "search"])
def test_an_empty_query_value_means_the_default(toolsets_client: ToolsetsClient, name: str) -> None:
    # Node forwards a parameter only when it is truthy, so an empty value is dropped.
    resp = toolsets_client.get("/instances", params={name: ""})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    pagination = resp.json()["pagination"]
    assert (pagination["page"], pagination["limit"]) == (1, 50)


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"limit": 0}, "Input should be greater than or equal to 1"),
        ({"limit": 201}, "Input should be less than or equal to 200"),
        ({"page": 0}, "Input should be greater than or equal to 1"),
        ({"page": "abc"}, "Input should be a valid integer, unable to parse string as an integer"),
    ],
    ids=["limit-zero", "limit-above-max", "page-zero", "page-not-a-number"],
)
def test_invalid_paging_is_refused_by_python(
    toolsets_client: ToolsetsClient, params: dict[str, int | str], message: str
) -> None:
    # No zod schema on this route: the query reaches FastAPI, whose 422 Node relays.
    resp = toolsets_client.get("/instances", params=params)
    assert resp.status_code == 422, resp.text[:500]
    error = error_of(resp)
    assert (error["code"], error["message"]) == ("HTTP_UNPROCESSABLE_ENTITY", message)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_a_repeated_search_is_joined_with_a_comma(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance()
    params: Any = [("search", seeded["instanceName"]), ("search", "second")]

    with outside_request_contract("no validator: Express hands over an array, which String() joins"):
        resp = toolsets_client.get("/instances", params=params)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    # The search term became "<name>,second", which no instance matches.
    assert resp.json()["instances"] == []


def test_unknown_query_parameters_are_ignored(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance()

    with outside_request_contract("only page, limit and search are forwarded to Python"):
        resp = toolsets_client.get(
            "/instances", params={"search": seeded["instanceName"], "toolsetType": "slack"}
        )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert [instance["_id"] for instance in resp.json()["instances"]] == [seeded["_id"]]


def test_instances_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.list_instances(auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
