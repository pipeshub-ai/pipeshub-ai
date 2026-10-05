"""Strict OpenAPI audit of GET /api/v1/skills/:name/resource."""

from __future__ import annotations

import pytest
from skills_audit_support import (
    MISSING_RESOURCE_PATH,
    MISSING_SKILL_NAME,
    RESOURCE_PATH,
    UNSAFE_PATH_SEGMENT,
    SeedSkill,
    SkillsClient,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/:name/resource"

# Deliberately not valid JSON: axios would parse a JSON-looking text/plain upstream body.
RESOURCE_TEXT = "# Spec audit reference\n\nplain text, not json\n"


def test_get_resource_returns_file_content(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]
    written = skills_client.put(
        f"/{name}/resource", json={"path": RESOURCE_PATH, "content": RESOURCE_TEXT}
    )
    assert written.status_code == 200, f"writing the resource failed: {written.text[:500]}"

    resp = skills_client.get(f"/{name}/resource", params={"path": RESOURCE_PATH})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    # Python answers text/plain; the Node proxy re-sends it through res.json as a JSON string.
    assert resp.json() == RESOURCE_TEXT


def test_get_resource_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.get(
        f"/{MISSING_SKILL_NAME}/resource", auth=False, params={"path": RESOURCE_PATH}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_get_resource_without_path_query_is_unprocessable(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]

    resp = skills_client.get(f"/{name}/resource")
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_get_missing_resource_of_existing_skill_is_not_found(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]

    resp = skills_client.get(f"/{name}/resource", params={"path": MISSING_RESOURCE_PATH})
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert "not found" in resp.json()["detail"].lower()


def test_get_resource_unsafe_name_is_rejected_by_path_guard(
    skills_client: SkillsClient,
) -> None:
    resp = skills_client.get(
        f"/{UNSAFE_PATH_SEGMENT}/resource", params={"path": RESOURCE_PATH}
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
