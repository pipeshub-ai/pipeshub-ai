"""Strict OpenAPI audit of GET /api/v1/skills/:name."""

from __future__ import annotations

import pytest
from skills_audit_support import (
    MISSING_SKILL_NAME,
    SKILL_BODY_MARKER,
    UNSAFE_PATH_SEGMENT,
    SeedSkill,
    SkillsClient,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/:name"


def test_get_returns_metadata_body_and_resources(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]

    resp = skills_client.fetch(name)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert body["name"] == name
    assert SKILL_BODY_MARKER in body["body"]
    assert isinstance(body["resources"], dict)


def test_get_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.fetch(MISSING_SKILL_NAME, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_get_missing_skill_is_not_found(skills_client: SkillsClient) -> None:
    resp = skills_client.fetch(MISSING_SKILL_NAME)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert "not found" in resp.json()["detail"].lower()


def test_get_another_users_skill_is_not_found(
    skills_client: SkillsClient, second_user: SecondUser, seed_skill: SeedSkill
) -> None:
    # Custom skills are creator-scoped: the admin must not be able to tell it exists.
    name = seed_skill(owner=second_user)["name"]

    resp = skills_client.fetch(name)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_get_unsafe_name_is_rejected_by_path_guard(skills_client: SkillsClient) -> None:
    resp = skills_client.fetch(UNSAFE_PATH_SEGMENT)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
