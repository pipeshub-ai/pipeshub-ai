"""Strict OpenAPI audit of POST /api/v1/skills."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from skills_audit_support import SkillsClient, skill_payload, unique_skill_name
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills"


def _skip_if_skills_disabled(resp: requests.Response) -> None:
    # The feature-flag dependency runs before body validation, so it masks every other outcome.
    if resp.status_code == 403:
        pytest.skip(f"skills are not usable on this stack: {resp.text[:200]}")


def test_create_returns_metadata_then_rejects_duplicate_name(
    skills_client: SkillsClient,
) -> None:
    name = unique_skill_name()
    payload = skill_payload(
        name,
        category="spec-audit",
        subcategory="created",
        tags=["spec-audit"],
        license="MIT",
        compatibility="Any agent",
        allowed_tools=["calculator"],
        concepts=["auditing"],
    )

    try:
        resp = skills_client.create(payload)
        _skip_if_skills_disabled(resp)
        assert resp.status_code == 201, resp.text[:500]
        assert_strict_openapi_response(resp, ROUTE)
        created = resp.json()
        assert created["name"] == name
        assert created["description"] == payload["description"]
        assert created["status"] == "active"
        assert created["source"] == "manual"
        assert created["category"] == "spec-audit"
        assert created["tags"] == ["spec-audit"]
        assert created["allowedTools"] == ["calculator"]

        again = skills_client.create(payload)
        assert again.status_code == 409, again.text[:500]
        assert_strict_openapi_response(again, ROUTE)
        assert again.json() == {"detail": f"Skill {name!r} already exists"}
    finally:
        skills_client.remove(name, detach="true")


def test_create_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.create(skill_payload(unique_skill_name()), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        # `name` is optional in the shared write model; the create handler rejects it itself.
        pytest.param(skill_payload(), 400, id="missing-name"),
        pytest.param({"name": "spec-audit-no-description", "body": "# x"}, 422, id="missing-description"),
    ],
)
def test_create_rejects_invalid_body(
    skills_client: SkillsClient, payload: dict[str, Any], expected_status: int
) -> None:
    resp = skills_client.create(payload)
    try:
        _skip_if_skills_disabled(resp)
        assert resp.status_code == expected_status, resp.text[:500]
        assert_strict_openapi_response(resp, ROUTE)
        if expected_status == 400:
            assert resp.json() == {"detail": "'name' is required to create a skill."}
    finally:
        if resp.status_code == 201:
            skills_client.remove(payload["name"], detach="true")


def test_create_under_builtin_name_is_conflict(
    skills_client: SkillsClient, builtin_skill_name: str
) -> None:
    resp = skills_client.create(skill_payload(builtin_skill_name))
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
