"""Strict OpenAPI audit of GET /api/v1/skills/:name/versions/:version."""

from __future__ import annotations

import pytest
from skills_audit_support import (
    MISSING_SKILL_NAME,
    MISSING_VERSION,
    SKILL_BODY_MARKER,
    UNSAFE_PATH_SEGMENT,
    SeedSkill,
    SkillsClient,
    skill_payload,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/:name/versions/:version"

UPDATED_BODY = "# Spec audit\n\nSecond revision.\n"


def test_get_version_returns_the_archived_revision(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]
    # A revision is archived only when the skill is overwritten.
    updated = skills_client.put(f"/{name}", json=skill_payload(body=UPDATED_BODY))
    assert updated.status_code == 200, updated.text[:500]
    listed = skills_client.get(f"/{name}/versions")
    assert listed.status_code == 200, listed.text[:500]
    versions = listed.json()["versions"]
    assert versions, f"no archived version after an update: {listed.text[:500]}"
    version = versions[0]["version"]

    resp = skills_client.get(f"/{name}/versions/{version}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert body["name"] == name
    assert SKILL_BODY_MARKER in body["body"]
    assert isinstance(body["resources"], dict)


def test_get_version_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.get(
        f"/{MISSING_SKILL_NAME}/versions/{MISSING_VERSION}", auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_get_unknown_version_of_existing_skill_is_not_found(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]

    resp = skills_client.get(f"/{name}/versions/{MISSING_VERSION}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert "not found" in resp.json()["detail"].lower()


def test_get_version_of_missing_skill_is_not_found(skills_client: SkillsClient) -> None:
    resp = skills_client.get(f"/{MISSING_SKILL_NAME}/versions/{MISSING_VERSION}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_get_unsafe_version_is_rejected_by_path_guard(skills_client: SkillsClient) -> None:
    resp = skills_client.get(f"/{MISSING_SKILL_NAME}/versions/{UNSAFE_PATH_SEGMENT}")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
