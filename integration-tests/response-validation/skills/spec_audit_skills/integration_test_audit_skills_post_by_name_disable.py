"""Strict OpenAPI audit of POST /api/v1/skills/:name/disable."""

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

ROUTE = "/api/v1/skills/:name/disable"


def test_disable_active_skill_then_conflicts(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]

    resp = skills_client.post(f"/{name}/disable")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert body["name"] == name
    assert body["status"] == "disabled"

    # Disable is only legal from "active".
    again = skills_client.post(f"/{name}/disable")
    assert again.status_code == 409, again.text[:500]
    assert_strict_openapi_response(again, ROUTE)


def test_disable_missing_skill_is_not_found(skills_client: SkillsClient) -> None:
    resp = skills_client.post(f"/{MISSING_SKILL_NAME}/disable")
    if resp.status_code == 403:
        pytest.skip(f"skills are not usable on this stack: {resp.text[:200]}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_cannot_disable_builtin_skill(
    second_user: SecondUser, builtin_skill_name: str
) -> None:
    # Refused before any transition, so the org-wide built-in stays untouched.
    resp = request_as(second_user, "POST", f"/{builtin_skill_name}/disable")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert "organization admin" in resp.json()["detail"]


def test_disable_unsafe_name_is_rejected(skills_client: SkillsClient) -> None:
    resp = skills_client.post(f"/{UNSAFE_PATH_SEGMENT}/disable")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_disable_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.post(f"/{MISSING_SKILL_NAME}/disable", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
