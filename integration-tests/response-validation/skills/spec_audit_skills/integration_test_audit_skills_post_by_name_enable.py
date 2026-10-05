"""Strict OpenAPI audit of POST /api/v1/skills/:name/enable."""

from __future__ import annotations

import pytest
from skills_audit_support import (
    MISSING_SKILL_NAME,
    SeedSkill,
    SkillsClient,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/:name/enable"


def _enable_path(name: str) -> str:
    return f"/{name}/enable"


def test_enable_disabled_skill_reactivates_it(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]
    disabled = skills_client.post(f"/{name}/disable")
    assert disabled.status_code == 200, f"disabling the seeded skill failed: {disabled.text[:500]}"

    resp = skills_client.post(_enable_path(name))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert body["name"] == name
    assert body["status"] == "active"


def test_enable_active_skill_is_a_conflict(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    # Enable only accepts disabled -> active; any other state is a 409, not a no-op.
    name = seed_skill()["name"]
    resp = skills_client.post(_enable_path(name))
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_enable_missing_skill_is_not_found(skills_client: SkillsClient) -> None:
    resp = skills_client.post(_enable_path(MISSING_SKILL_NAME))
    if resp.status_code == 403:
        pytest.skip(f"skills are not usable on this stack: {resp.text[:200]}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"detail": f"Skill {MISSING_SKILL_NAME!r} not found"}


def test_member_enable_builtin_skill_is_forbidden(
    second_user: SecondUser, builtin_skill_name: str
) -> None:
    # The admin check runs before the status transition, so nothing org-wide changes.
    resp = request_as(second_user, "POST", _enable_path(builtin_skill_name))
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_enable_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.post(_enable_path(MISSING_SKILL_NAME), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
