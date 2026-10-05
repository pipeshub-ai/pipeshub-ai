"""Strict OpenAPI audit of GET /api/v1/skills."""

from __future__ import annotations

import uuid

import pytest
from helper.second_user import SecondUser
from skills_audit_support import SeedSkill, SkillsClient
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills"


def _unique_category() -> str:
    return f"spec-audit-cat-{uuid.uuid4().hex[:10]}"


def test_list_filtered_by_category_returns_own_skill(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    category = _unique_category()
    name = seed_skill(category=category, tags=["spec-audit"])["name"]

    resp = skills_client.list(category=category, status="active", source="manual")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    skills = resp.json()["skills"]
    assert [skill["name"] for skill in skills] == [name]
    assert skills[0]["category"] == category
    # Unlike the create response, a read carries the timestamps the store assigned.
    assert isinstance(skills[0]["createdAt"], str)


def test_list_includes_builtin_skills(skills_client: SkillsClient) -> None:
    resp = skills_client.list(source="builtin")
    if resp.status_code == 403:
        pytest.skip(f"skills are not usable on this stack: {resp.text[:200]}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert all(skill["source"] == "builtin" for skill in resp.json()["skills"])


def test_list_does_not_return_another_users_skill(
    skills_client: SkillsClient, seed_skill: SeedSkill, second_user: SecondUser
) -> None:
    category = _unique_category()
    seed_skill(owner=second_user, category=category)

    resp = skills_client.list(category=category)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"skills": []}


@pytest.mark.xfail(
    strict=True,
    reason="API bug: a `status` filter outside the enum is not validated and answers 500",
)
def test_list_unknown_status_filter_is_unprocessable(skills_client: SkillsClient) -> None:
    resp = skills_client.list(status="spec-audit-not-a-status")
    if resp.status_code == 403:
        pytest.skip(f"skills are not usable on this stack: {resp.text[:200]}")
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_list_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.list(auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
