"""Strict OpenAPI audit of GET /api/v1/skills/:name."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from skills_audit_support import (
    MALFORMED_SKILL_NAME,
    MISSING_SKILL_NAME,
    RESOURCE_PATH,
    SKILL_BODY_MARKER,
    UNSAFE_PATH_SEGMENT,
    SeedSkill,
    SkillsClient,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/:name"


def test_get_returns_metadata_body_and_resources(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]
    written = skills_client.put(f"/{name}/resource", json={"path": RESOURCE_PATH, "content": "x\n"})
    assert written.status_code == 200, written.text[:500]

    resp = skills_client.fetch(name)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["name"] == name
    assert SKILL_BODY_MARKER in body["body"]
    assert body["resources"] == {"references": [RESOURCE_PATH]}
    assert isinstance(body["createdAt"], str) and isinstance(body["updatedAt"], str)


def test_get_builtin_skill(skills_client: SkillsClient, builtin_skill_name: str) -> None:
    resp = skills_client.fetch(builtin_skill_name)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["source"] == "builtin"


def test_get_ignores_query_parameters(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    name = seed_skill()["name"]
    with outside_request_contract("the route documents no query parameter; this shows one is ignored"):
        resp = skills_client.get(f"/{name}", params={"version": "0.0.1"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["name"] == name


def test_get_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.fetch(MISSING_SKILL_NAME, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("name", [MISSING_SKILL_NAME, MALFORMED_SKILL_NAME], ids=["missing", "not-kebab-case"])
def test_get_missing_skill_is_not_found(skills_client: SkillsClient, name: str) -> None:
    resp = skills_client.fetch(name)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"detail": f"Skill {name!r} not found"}


def test_get_another_users_skill_is_not_found(
    skills_client: SkillsClient, second_user: SecondUser, seed_skill: SeedSkill
) -> None:
    # Custom skills are creator-scoped: the admin must not be able to tell it exists.
    name = seed_skill(owner=second_user)["name"]

    resp = skills_client.fetch(name)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_get_unsafe_name_is_rejected_by_path_guard(skills_client: SkillsClient) -> None:
    resp = skills_client.fetch(UNSAFE_PATH_SEGMENT)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
