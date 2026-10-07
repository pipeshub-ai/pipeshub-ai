"""Strict OpenAPI audit of GET /api/v1/skills/:name/export."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from skills_audit_support import (
    MISSING_SKILL_NAME,
    SKILL_BODY_MARKER,
    UNSAFE_PATH_SEGMENT,
    SeedSkill,
    SkillsClient,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/:name/export"


def test_export_returns_skill_md_attachment(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]

    resp = skills_client.get(f"/{name}/export")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.headers["Content-Type"].startswith("text/markdown")
    assert resp.headers.get("Content-Disposition") == f'attachment; filename="{name}.SKILL.md"'
    assert resp.text.startswith("---\n")
    assert f"name: {name}" in resp.text
    assert SKILL_BODY_MARKER in resp.text


def test_export_builtin_skill(skills_client: SkillsClient, builtin_skill_name: str) -> None:
    resp = skills_client.get(f"/{builtin_skill_name}/export")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert f"name: {builtin_skill_name}" in resp.text


def test_query_parameter_is_ignored(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    name = seed_skill()["name"]
    with outside_request_contract("an undocumented query parameter, to show it is ignored"):
        resp = skills_client.get(f"/{name}/export", params={"spec_audit_unknown": "1"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_export_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.get(f"/{MISSING_SKILL_NAME}/export", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_export_missing_skill_is_not_found(skills_client: SkillsClient) -> None:
    resp = skills_client.get(f"/{MISSING_SKILL_NAME}/export")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    # The controller copies Python's content type, so the error stays JSON.
    assert resp.headers["Content-Type"].startswith("application/json")
    assert "not found" in resp.json()["detail"].lower()


def test_export_another_users_skill_is_not_found(
    skills_client: SkillsClient, second_user: SecondUser, seed_skill: SeedSkill
) -> None:
    # Custom skills are creator-scoped: the admin must not be able to tell it exists.
    name = seed_skill(owner=second_user)["name"]

    resp = skills_client.get(f"/{name}/export")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_export_unsafe_name_is_rejected_by_path_guard(skills_client: SkillsClient) -> None:
    resp = skills_client.get(f"/{UNSAFE_PATH_SEGMENT}/export")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
