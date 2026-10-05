"""Strict OpenAPI audit of GET /api/v1/skills/search."""

from __future__ import annotations

import uuid

import pytest
from skills_audit_support import SeedSkill, SkillsClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

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
    assert_strict_openapi_response(resp, ROUTE)

    results = resp.json()["results"]
    assert [r["skill"]["name"] for r in results] == [name]
    assert results[0]["skill"]["category"] == category
    assert results[0]["relevance"] == 1.0
    assert results[0]["matchReason"] == "catalog"


def test_search_does_not_return_another_users_skill(
    skills_client: SkillsClient, seed_skill: SeedSkill, second_user: SecondUser
) -> None:
    category = _unique_category()
    seed_skill(owner=second_user, category=category)

    resp = skills_client.get("/search", params={"category": category})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"results": []}


@pytest.mark.parametrize("limit", ["0", "not-a-number"])
def test_search_invalid_limit_is_unprocessable(
    skills_client: SkillsClient, limit: str
) -> None:
    resp = skills_client.get("/search", params={"limit": limit})
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_search_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.get("/search", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
