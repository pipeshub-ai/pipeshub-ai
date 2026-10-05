"""Strict OpenAPI audit of POST /api/v1/skills/:name/deprecate."""

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

ROUTE = "/api/v1/skills/:name/deprecate"

DEPRECATE_BODY = {"reason": "Superseded during the spec audit", "replaced_by": MISSING_SKILL_NAME}


def _deprecate_path(name: str) -> str:
    return f"/{name}/deprecate"


def test_deprecate_marks_skill_deprecated(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]

    resp = skills_client.post(_deprecate_path(name), json=DEPRECATE_BODY)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"status": "success"}

    stored = skills_client.fetch(name)
    assert stored.status_code == 200, stored.text[:500]
    metadata = stored.json()
    assert metadata["status"] == "deprecated"
    assert metadata["deprecatedReason"] == DEPRECATE_BODY["reason"]
    assert metadata["replacedBy"] == DEPRECATE_BODY["replaced_by"]


def test_deprecate_without_reason_is_unprocessable(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]

    # Python's DeprecateRequest requires a non-empty reason; Node has no validator of its own.
    resp = skills_client.post(_deprecate_path(name), json={"replaced_by": MISSING_SKILL_NAME})
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    stored = skills_client.fetch(name)
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json()["status"] == "active"


def test_member_deprecating_admins_skill_is_not_found(
    skills_client: SkillsClient, second_user: SecondUser, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]

    # Custom skills are creator-scoped: a non-owner cannot see the skill, so no 403.
    resp = request_as(second_user, "POST", _deprecate_path(name), json=DEPRECATE_BODY)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    stored = skills_client.fetch(name)
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json()["status"] == "active"


def test_deprecate_unsafe_name_is_rejected(skills_client: SkillsClient) -> None:
    resp = skills_client.post(_deprecate_path(UNSAFE_PATH_SEGMENT), json=DEPRECATE_BODY)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_deprecate_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.post(_deprecate_path(MISSING_SKILL_NAME), json=DEPRECATE_BODY, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
