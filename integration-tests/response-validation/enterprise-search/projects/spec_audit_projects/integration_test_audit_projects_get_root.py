"""Strict OpenAPI audit of GET /api/v1/projects."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from helper.clients.projects_client import ProjectsClient
from helper.second_user import SecondUser
from projects_audit_support import (
    PROJECT_FIELDS,
    ROOT_TEMPLATE,
    SeedProject,
    add_member,
    request_as,
    validation_fields,
)
from strict_openapi import (
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = ROOT_TEMPLATE


def _ids(resp: Any) -> list[str]:
    return [p["_id"] for p in resp.json()["projects"]]


def test_list_own_projects_with_defaults(
    projects_client: ProjectsClient, seed_project: SeedProject
) -> None:
    tag = uuid.uuid4().hex[:8]
    first = seed_project(name=f"spec-audit list {tag} a")
    second = seed_project(name=f"spec-audit list {tag} b")

    resp = projects_client.get("/", params={"search": tag})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["pagination"] == {"page": 1, "limit": 20, "totalCount": 2, "totalPages": 1}
    # Most recent activity first.
    assert _ids(resp) == [second["_id"], first["_id"]]
    row = body["projects"][0]
    assert set(row) <= PROJECT_FIELDS | {"conversationCount"}
    assert row["role"] == "owner"
    assert row["conversationCount"] == 0


def test_list_paginates_and_puts_pinned_first(
    projects_client: ProjectsClient, seed_project: SeedProject
) -> None:
    tag = uuid.uuid4().hex[:8]
    pinned = seed_project(name=f"spec-audit page {tag} 1")
    others = [seed_project(name=f"spec-audit page {tag} {i}") for i in (2, 3)]
    assert projects_client.pin_project(pinned["_id"]).status_code == 200

    page_one = projects_client.get("/", params={"search": tag, "limit": "2", "page": "1"})
    page_two = projects_client.get("/", params={"search": tag, "limit": 2, "page": 2})

    for resp in (page_one, page_two):
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert page_one.json()["pagination"] == {"page": 1, "limit": 2, "totalCount": 3, "totalPages": 2}
    assert _ids(page_one) == [pinned["_id"], others[1]["_id"]]
    assert _ids(page_two) == [others[0]["_id"]]


def test_list_accepts_fractional_page_and_limit(
    projects_client: ProjectsClient, seed_project: SeedProject
) -> None:
    tag = uuid.uuid4().hex[:8]
    oldest, middle, newest = (seed_project(name=f"spec-audit frac {tag} {i}") for i in range(3))

    half_page = projects_client.get("/", params={"search": tag, "limit": "2", "page": "1.5"})
    assert half_page.status_code == 200, half_page.text[:500]
    assert_strict_openapi_exchange(half_page, ROUTE)
    # skip = (page - 1) * limit = 1, and the fractional page is echoed back.
    assert half_page.json()["pagination"] == {"page": 1.5, "limit": 2, "totalCount": 3, "totalPages": 2}
    assert _ids(half_page) == [middle["_id"], oldest["_id"]]

    odd_limit = projects_client.get("/", params={"search": tag, "limit": "1.5", "page": "1"})
    assert odd_limit.status_code == 200, odd_limit.text[:500]
    assert_strict_openapi_exchange(odd_limit, ROUTE)
    assert odd_limit.json()["pagination"] == {"page": 1, "limit": 1.5, "totalCount": 3, "totalPages": 2}
    assert _ids(odd_limit) == [newest["_id"]]


def test_list_accepts_exponent_notation_page(
    projects_client: ProjectsClient, seed_project: SeedProject
) -> None:
    tag = uuid.uuid4().hex[:8]
    seed_project(name=f"spec-audit exp {tag}")

    # The spec types page as a number, so "1e0" is inside the contract; the checker only
    # coerces plain integers and decimals from the query string.
    with outside_request_contract("the checker cannot coerce exponent notation in a query value"):
        resp = projects_client.get("/", params={"search": tag, "page": "1e0"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["pagination"]["page"] == 1
    assert len(resp.json()["projects"]) == 1


def test_list_archive_filters(projects_client: ProjectsClient, seed_project: SeedProject) -> None:
    tag = uuid.uuid4().hex[:8]
    active = seed_project(name=f"spec-audit arch {tag} active")
    archived = seed_project(name=f"spec-audit arch {tag} archived")
    assert projects_client.archive_project(archived["_id"]).status_code == 200

    cases = {
        (): {active["_id"]},
        (("includeArchived", "false"),): {active["_id"]},
        (("includeArchived", "true"),): {active["_id"], archived["_id"]},
        (("isArchived", "true"),): {archived["_id"]},
        (("isArchived", "false"),): {active["_id"]},
        (("isArchived", "true"), ("includeArchived", "false")): {archived["_id"]},
    }
    for extra, expected in cases.items():
        resp = projects_client.get("/", params={"search": tag, **dict(extra)})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert set(_ids(resp)) == expected, extra


def test_list_scopes(
    projects_client: ProjectsClient, second_user: SecondUser, seed_project: SeedProject
) -> None:
    tag = uuid.uuid4().hex[:8]
    member_of = seed_project(name=f"spec-audit scope {tag} member")
    org_wide = seed_project(name=f"spec-audit scope {tag} org")
    seed_project(name=f"spec-audit scope {tag} private")
    add_member(projects_client, member_of["_id"], second_user.user_id, "editor")
    assert projects_client.update_project(org_wide["_id"], visibility="org").status_code == 200

    expected = {
        "mine": set(),
        "shared": {member_of["_id"]},
        "all": {member_of["_id"], org_wide["_id"]},
    }
    for scope, ids in expected.items():
        resp = request_as(second_user, "GET", params={"search": tag, "scope": scope})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert set(_ids(resp)) == ids, scope
        roles = {p["_id"]: p["role"] for p in resp.json()["projects"]}
        assert roles.get(member_of["_id"], "editor") == "editor"
        assert roles.get(org_wide["_id"], "viewer") == "viewer"


def test_list_search_is_literal_and_case_insensitive(
    projects_client: ProjectsClient, seed_project: SeedProject
) -> None:
    tag = uuid.uuid4().hex[:8]
    project = seed_project(name=f"Spec-Audit (x.y) {tag}")

    resp = projects_client.get("/", params={"search": f"(X.Y) {tag.upper()}"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _ids(resp) == [project["_id"]]

    regex = projects_client.get("/", params={"search": f".* {tag}"})
    assert regex.status_code == 200, regex.text[:500]
    assert_strict_openapi_exchange(regex, ROUTE)
    assert _ids(regex) == []


def test_list_ignores_unknown_query_parameters(projects_client: ProjectsClient) -> None:
    with outside_request_contract("unknown query parameters are stripped by the validator, not refused"):
        resp = projects_client.get("/", params={"limit": 1, "sort": "name", "userId": "x"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["pagination"]["limit"] == 1


@pytest.mark.parametrize(
    ("params", "field"),
    [
        pytest.param({"page": "0"}, "query.page", id="page-zero"),
        pytest.param({"page": "abc"}, "query.page", id="page-not-number"),
        pytest.param({"limit": "0"}, "query.limit", id="limit-zero"),
        pytest.param({"limit": "101"}, "query.limit", id="limit-too-big"),
        pytest.param({"search": "s" * 201}, "query.search", id="search-too-long"),
        pytest.param({"scope": "everything"}, "query.scope", id="scope-unknown"),
        pytest.param({"includeArchived": "yes"}, "query.includeArchived", id="include-archived-not-boolean"),
        pytest.param({"isArchived": "1"}, "query.isArchived", id="is-archived-not-boolean"),
        pytest.param({"scope": ""}, "query.scope", id="scope-empty"),
        pytest.param({"page": ["1", "2"]}, "query.page", id="page-repeated"),
        pytest.param({"scope": ["mine", "all"]}, "query.scope", id="scope-repeated"),
    ],
)
def test_list_rejects_invalid_query(
    projects_client: ProjectsClient, params: dict[str, Any], field: str
) -> None:
    resp = projects_client.get("/", params=params)

    assert validation_fields(resp) == {field}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_projects_without_token_is_unauthorized(projects_client: ProjectsClient) -> None:
    resp = projects_client.get("/", auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
