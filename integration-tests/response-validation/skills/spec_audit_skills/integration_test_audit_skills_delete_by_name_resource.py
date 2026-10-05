"""Strict OpenAPI audit of DELETE /api/v1/skills/:name/resource."""

from __future__ import annotations

import pytest
from skills_audit_support import (
    MISSING_RESOURCE_PATH,
    MISSING_SKILL_NAME,
    RESOURCE_PATH,
    SeedSkill,
    SkillsClient,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/:name/resource"


def test_delete_removes_resource_then_reports_not_found(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]
    written = skills_client.put(
        f"/{name}/resource", json={"path": RESOURCE_PATH, "content": "spec audit resource\n"}
    )
    assert written.status_code == 200, f"seeding a resource failed: {written.text[:500]}"

    resp = skills_client.delete(f"/{name}/resource", params={"path": RESOURCE_PATH})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"status": "success"}

    again = skills_client.delete(f"/{name}/resource", params={"path": RESOURCE_PATH})
    assert again.status_code == 404, again.text[:500]
    assert_strict_openapi_response(again, ROUTE)


def test_delete_unknown_resource_of_existing_skill_is_not_found(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]

    resp = skills_client.delete(f"/{name}/resource", params={"path": MISSING_RESOURCE_PATH})
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert MISSING_RESOURCE_PATH in resp.json()["detail"]

    stored = skills_client.fetch(name)
    assert stored.status_code == 200, stored.text[:500]


def test_delete_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.delete(
        f"/{MISSING_SKILL_NAME}/resource", auth=False, params={"path": RESOURCE_PATH}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_delete_without_path_query_is_unprocessable(skills_client: SkillsClient) -> None:
    # FastAPI validates the required query before the handler runs, so no skill needs to exist.
    resp = skills_client.delete(f"/{MISSING_SKILL_NAME}/resource")
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_delete_resource_of_builtin_skill_is_forbidden(
    skills_client: SkillsClient, builtin_skill_name: str
) -> None:
    # The built-in guard runs before the resource lookup, so the path need not exist.
    resp = skills_client.delete(
        f"/{builtin_skill_name}/resource", params={"path": MISSING_RESOURCE_PATH}
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert "built-in" in resp.json()["detail"]
