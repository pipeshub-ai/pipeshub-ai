"""Strict OpenAPI audit of POST /api/v1/skills/:name/rollback."""

from __future__ import annotations

import pytest
from skills_audit_support import (
    MISSING_SKILL_NAME,
    MISSING_VERSION,
    SeedSkill,
    SkillsClient,
    skill_payload,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/:name/rollback"

ORIGINAL_DESCRIPTION = "Spec audit rollback: original revision"
EDITED_DESCRIPTION = "Spec audit rollback: edited revision"


def test_rollback_restores_archived_version_as_new_revision(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    seeded = seed_skill(description=ORIGINAL_DESCRIPTION)
    name, original_version = seeded["name"], seeded["version"]

    # A version only becomes a rollback target once an update has archived it.
    edited = skills_client.put(f"/{name}", json=skill_payload(description=EDITED_DESCRIPTION))
    assert edited.status_code == 200, edited.text[:500]
    edited_version = edited.json()["version"]

    resp = skills_client.post(f"/{name}/rollback", json={"version": original_version})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert body["name"] == name
    assert body["description"] == ORIGINAL_DESCRIPTION
    # The restored copy gets a fresh version number; history never rewinds.
    assert body["version"] not in (original_version, edited_version)

    unknown = skills_client.post(f"/{name}/rollback", json={"version": MISSING_VERSION})
    assert unknown.status_code == 404, unknown.text[:500]
    assert_strict_openapi_response(unknown, ROUTE)
    assert MISSING_VERSION in unknown.json()["detail"]


def test_rollback_missing_skill_is_not_found(skills_client: SkillsClient) -> None:
    resp = skills_client.post(f"/{MISSING_SKILL_NAME}/rollback", json={"version": "1.0.0"})
    if resp.status_code == 403:
        pytest.skip(f"skills are not usable on this stack: {resp.text[:200]}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_rollback_without_version_is_unprocessable(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]

    resp = skills_client.post(f"/{name}/rollback", json={})
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert isinstance(resp.json()["detail"], list)


def test_rollback_builtin_skill_is_forbidden(
    skills_client: SkillsClient, builtin_skill_name: str
) -> None:
    # Refused before the store is touched, so the org-wide built-in stays as it is.
    resp = skills_client.post(f"/{builtin_skill_name}/rollback", json={"version": "1.0.0"})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert "built-in skill" in resp.json()["detail"]


def test_rollback_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.post(
        f"/{MISSING_SKILL_NAME}/rollback", json={"version": "1.0.0"}, auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
