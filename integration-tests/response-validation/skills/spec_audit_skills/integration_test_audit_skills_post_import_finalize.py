"""Strict OpenAPI audit of POST /api/v1/skills/import/finalize.

The import routes share a 10 requests/minute per-user limiter that counts
refused requests too, so this file makes three admin calls, one member call
and one anonymous call.
"""

from __future__ import annotations

import pytest
from skills_audit_support import (
    RESOURCE_PATH,
    SeedSkill,
    SkillsClient,
    request_as,
    skill_md,
    unique_skill_name,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/import/finalize"
FINALIZE = "/import/finalize"


def _skip_if_skills_disabled(status_code: int, text: str) -> None:
    if status_code == 403:
        pytest.skip(f"skills are not usable on this stack: {text[:200]}")


def test_finalize_creates_skill_with_resources(skills_client: SkillsClient) -> None:
    name = unique_skill_name()
    try:
        resp = skills_client.post(
            FINALIZE,
            json={
                "content": skill_md(name),
                "resources": {RESOURCE_PATH: "# Spec audit reference\n"},
                "category": "spec-audit",
            },
        )
        _skip_if_skills_disabled(resp.status_code, resp.text)
        assert resp.status_code == 201, resp.text[:500]
        assert_strict_openapi_response(resp, ROUTE)
        assert resp.json()["name"] == name
    finally:
        skills_client.remove(name, detach="true")


def test_finalize_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.post(
        FINALIZE, auth=False, json={"content": skill_md(unique_skill_name())}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_finalize_without_content_is_unprocessable(skills_client: SkillsClient) -> None:
    # Node has no validator here: the pydantic 422 from Python passes through.
    resp = skills_client.post(FINALIZE, json={"resources": {}})
    _skip_if_skills_disabled(resp.status_code, resp.text)
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_finalize_without_frontmatter_name_is_bad_request(
    second_user: SecondUser,
) -> None:
    resp = request_as(
        second_user, "POST", FINALIZE, json={"content": "# No frontmatter here\n"}
    )
    _skip_if_skills_disabled(resp.status_code, resp.text)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"detail": "Imported SKILL.md is missing a 'name' field."}


def test_finalize_existing_name_is_conflict(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]

    resp = skills_client.post(FINALIZE, json={"content": skill_md(name)})
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"detail": f"Skill {name!r} already exists"}
