"""Strict OpenAPI audit of GET /api/v1/skills/categories."""

from __future__ import annotations

import uuid

import pytest
from skills_audit_support import SeedSkill, SkillsClient, request_as
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/categories"


def _unique(prefix: str) -> str:
    return f"spec-audit-{prefix}-{uuid.uuid4().hex[:8]}"


def test_categories_map_each_category_to_its_subcategories(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    category, subcategory, tag = _unique("cat"), _unique("sub"), _unique("tag")
    seed_skill(category=category, subcategory=subcategory, tags=[tag])

    resp = skills_client.get("/categories")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert set(body) == {"categories", "tags"}
    # Python returns {category: [subcategory, ...]}, not a flat list of names.
    assert isinstance(body["categories"], dict), body["categories"]
    assert body["categories"][category] == [subcategory]
    assert tag in body["tags"]
    assert body["tags"] == sorted(body["tags"])


def test_member_does_not_see_categories_of_another_users_skill(
    second_user: SecondUser, seed_skill: SeedSkill
) -> None:
    admin_category, admin_tag = _unique("cat"), _unique("tag")
    member_category = _unique("cat")
    seed_skill(category=admin_category, tags=[admin_tag])
    seed_skill(owner=second_user, category=member_category)

    resp = request_as(second_user, "GET", "/categories")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    # A category without subcategories is still listed, with an empty list.
    assert body["categories"][member_category] == []
    assert admin_category not in body["categories"]
    assert admin_tag not in body["tags"]


def test_categories_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.get("/categories", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_categories_with_invalid_token_is_unauthorized(
    skills_client: SkillsClient,
) -> None:
    resp = skills_client.get(
        "/categories", auth=False, headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
