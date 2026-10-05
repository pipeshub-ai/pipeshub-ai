"""Strict OpenAPI audit of GET /api/v1/skills/:name/usage."""

from __future__ import annotations

import pytest
from skills_audit_support import (
    MISSING_SKILL_NAME,
    UNSAFE_PATH_SEGMENT,
    SeedSkill,
    SkillsClient,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/:name/usage"

UNUSED = {"usedByAgents": [], "requiredBySkills": []}


def test_usage_of_unreferenced_skill_is_empty(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]

    resp = skills_client.get(f"/{name}/usage")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == UNUSED


def test_usage_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.get(f"/{MISSING_SKILL_NAME}/usage", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_usage_of_missing_skill_is_empty_not_404(skills_client: SkillsClient) -> None:
    # The handler only reads inbound graph edges; it never looks the skill up.
    resp = skills_client.get(f"/{MISSING_SKILL_NAME}/usage")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == UNUSED


def test_usage_of_another_users_skill_is_not_creator_scoped(
    second_user: SecondUser, seed_skill: SeedSkill
) -> None:
    # Unlike GET /:name (404 for a co-worker's skill), usage is keyed by org and name only.
    name = seed_skill()["name"]

    resp = request_as(second_user, "GET", f"/{name}/usage")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == UNUSED


def test_usage_unsafe_name_is_rejected_by_path_guard(skills_client: SkillsClient) -> None:
    resp = skills_client.get(f"/{UNSAFE_PATH_SEGMENT}/usage")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
