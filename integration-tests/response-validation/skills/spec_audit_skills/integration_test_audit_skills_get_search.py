"""Strict OpenAPI audit of GET /api/v1/skills/search."""

from __future__ import annotations

import uuid

import pytest
from helper.second_user import SecondUser
from skills_audit_support import SeedSkill, SkillsClient
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/search"


def _unique_category() -> str:
    return f"spec-audit-cat-{uuid.uuid4().hex[:10]}"


def test_search_by_category_returns_own_skill(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    category = _unique_category()
    name = seed_skill(category=category)["name"]

    # An empty q takes the catalog branch: no query embedding, fixed relevance.
    resp = skills_client.get("/search", params={"category": category, "limit": 5})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    results = resp.json()["results"]
    assert [r["skill"]["name"] for r in results] == [name]
    assert results[0]["skill"]["category"] == category
    assert results[0]["relevance"] == 1.0
    assert results[0]["matchReason"] == "catalog"


def test_search_with_query_ranks_builtin_skills(skills_client: SkillsClient) -> None:
    resp = skills_client.get("/search", params={"q": "word document docx", "limit": 3})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    results = resp.json()["results"]
    assert 0 < len(results) <= 3
    assert all(isinstance(r["relevance"], (int, float)) for r in results)


def test_search_does_not_return_another_users_skill(
    skills_client: SkillsClient, seed_skill: SeedSkill, second_user: SecondUser
) -> None:
    category = _unique_category()
    seed_skill(owner=second_user, category=category)

    resp = skills_client.get("/search", params={"category": category})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"results": []}


@pytest.mark.parametrize(
    ("limit", "message"),
    [
        ("0", "Limit must be at least 1."),
        ("101", "Limit must be at most 100."),
        ("not-a-number", None),
    ],
)
def test_search_invalid_limit_is_unprocessable(
    skills_client: SkillsClient, limit: str, message: str | None
) -> None:
    with outside_request_contract("a limit the spec does not allow"):
        resp = skills_client.get("/search", params={"limit": limit})
        assert resp.status_code == 422, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["detail"][0]["loc"] == ["query", "limit"]
    if message is not None:
        assert resp.json()["message"] == message
    assert_spec_forbids_request(resp, ROUTE)


def test_search_ignores_unknown_query_parameter(skills_client: SkillsClient) -> None:
    with outside_request_contract("an undocumented query parameter, to show it is ignored"):
        resp = skills_client.get(
            "/search", params={"category": _unique_category(), "spec_audit_unknown": "1"}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"results": []}


def test_search_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.get("/search", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
