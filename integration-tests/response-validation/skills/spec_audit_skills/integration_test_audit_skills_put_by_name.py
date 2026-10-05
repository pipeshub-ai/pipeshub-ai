"""Strict OpenAPI audit of PUT /api/v1/skills/:name."""

from __future__ import annotations

import pytest
import requests
from skills_audit_support import (
    MISSING_SKILL_NAME,
    SeedSkill,
    SkillsClient,
    skill_payload,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/:name"

UPDATED_DESCRIPTION = "Updated by the skills spec audit"


def _skip_if_skills_disabled(resp: requests.Response) -> None:
    # The feature-flag dependency answers 403 before any handler logic runs.
    if resp.status_code == 403:
        assert_strict_openapi_response(resp, ROUTE)
        pytest.skip(f"skills are not usable on this stack: {resp.text[:200]}")


def test_admin_updates_own_skill(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    name = seed_skill()["name"]
    # A body `name` is ignored on update: the path parameter is authoritative.
    payload = skill_payload(
        "spec-audit-ignored-name", description=UPDATED_DESCRIPTION, tags=["spec-audit"]
    )
    resp = skills_client.put(f"/{name}", json=payload)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert body["name"] == name
    assert body["description"] == UPDATED_DESCRIPTION
    assert body["tags"] == ["spec-audit"]


def test_stale_if_match_is_conflict(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    name = seed_skill()["name"]
    resp = skills_client.put(f"/{name}", json=skill_payload(), headers={"If-Match": "1"})
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    # Unlike every other error of this route, `detail` is an object here.
    detail = resp.json()["detail"]
    assert {"message", "currentUpdatedAt", "currentVersion"} <= set(detail)


def test_missing_skill_is_not_found(skills_client: SkillsClient) -> None:
    resp = skills_client.put(f"/{MISSING_SKILL_NAME}", json=skill_payload())
    _skip_if_skills_disabled(resp)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_body_without_required_fields_is_unprocessable(skills_client: SkillsClient) -> None:
    # Pydantic rejects the body before the skill is looked up, so no seed is needed.
    resp = skills_client.put(f"/{MISSING_SKILL_NAME}", json={"description": ""})
    _skip_if_skills_disabled(resp)
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.put(f"/{MISSING_SKILL_NAME}", json=skill_payload(), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
