"""Strict OpenAPI audit of DELETE /api/v1/skills/:name."""

from __future__ import annotations

import pytest
from skills_audit_support import (
    MISSING_SKILL_NAME,
    UNSAFE_PATH_SEGMENT,
    SeedSkill,
    SkillsClient,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/:name"


def test_delete_removes_skill_then_reports_not_found(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]

    resp = skills_client.remove(name)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"status": "success"}

    again = skills_client.remove(name)
    assert again.status_code == 404, again.text[:500]
    assert_strict_openapi_response(again, ROUTE)
    assert "not found" in again.json()["detail"].lower()


def test_delete_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.remove(MISSING_SKILL_NAME, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_delete_builtin_skill_is_forbidden(
    skills_client: SkillsClient, builtin_skill_name: str
) -> None:
    # The built-in check runs before the delete, so even detach=true cannot remove one.
    resp = skills_client.remove(builtin_skill_name, detach="true")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert "built-in" in resp.json()["detail"]


def test_delete_non_boolean_detach_is_unprocessable(skills_client: SkillsClient) -> None:
    # FastAPI validates the query before the handler runs, so no skill needs to exist.
    resp = skills_client.remove(MISSING_SKILL_NAME, detach="not-a-bool")
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_delete_unsafe_name_is_rejected_by_path_guard(skills_client: SkillsClient) -> None:
    resp = skills_client.remove(UNSAFE_PATH_SEGMENT)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
