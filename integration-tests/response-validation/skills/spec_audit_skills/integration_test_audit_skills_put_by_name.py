"""Strict OpenAPI audit of PUT /api/v1/skills/:name."""

from __future__ import annotations

from typing import Any

import pytest
from helper.second_user import SecondUser
from skills_audit_support import (
    MISSING_SKILL_NAME,
    SeedSkill,
    SkillsClient,
    skill_payload,
)
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/:name"

UPDATED_DESCRIPTION = "Updated by the skills spec audit"


def test_admin_updates_own_skill(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    name = seed_skill()["name"]
    payload = skill_payload(description=UPDATED_DESCRIPTION, tags=["spec-audit"])
    resp = skills_client.put(f"/{name}", json=payload)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["name"] == name
    assert body["description"] == UPDATED_DESCRIPTION
    assert body["tags"] == ["spec-audit"]
    assert body["version"] == "1.0.1"


def test_body_name_is_ignored(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    name = seed_skill()["name"]
    resp = skills_client.put(f"/{name}", json=skill_payload("spec-audit-ignored-name"))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["name"] == name


def test_unknown_body_field_is_ignored(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    name = seed_skill()["name"]
    with outside_request_contract("an undocumented body field, to show it is ignored"):
        resp = skills_client.put(f"/{name}", json=skill_payload(spec_audit_unknown=1))
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("form", ["{ts}", '"{ts}"', 'W/"{ts}"', "*"], ids=["bare", "quoted", "weak", "any"])
def test_matching_if_match_is_accepted(
    skills_client: SkillsClient, seed_skill: SeedSkill, form: str
) -> None:
    name = seed_skill()["name"]
    updated_at = skills_client.fetch(name).json()["updatedAt"]
    resp = skills_client.put(
        f"/{name}", json=skill_payload(description=UPDATED_DESCRIPTION),
        headers={"If-Match": form.format(ts=updated_at)},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_stale_if_match_is_conflict(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    name = seed_skill()["name"]
    resp = skills_client.put(f"/{name}", json=skill_payload(), headers={"If-Match": "1"})
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    # Unlike every other error of this route, `detail` is an object here.
    detail = resp.json()["detail"]
    assert {"message", "currentUpdatedAt", "currentVersion"} == set(detail)


def test_non_numeric_if_match_is_bad_request(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    name = seed_skill()["name"]
    resp = skills_client.put(f"/{name}", json=skill_payload(), headers={"If-Match": "spec-audit"})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"detail": "If-Match must be the skill's updatedAt timestamp"}


def test_invalid_rendered_skill_md_is_bad_request(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    name = seed_skill()["name"]
    resp = skills_client.put(f"/{name}", json=skill_payload(description="d" * 1025))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "1024" in resp.json()["detail"]


def test_builtin_skill_is_read_only(skills_client: SkillsClient, builtin_skill_name: str) -> None:
    resp = skills_client.put(f"/{builtin_skill_name}", json=skill_payload())
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"detail": f"{builtin_skill_name!r} is a built-in skill and cannot be modified."}


def test_missing_skill_is_not_found(skills_client: SkillsClient) -> None:
    resp = skills_client.put(f"/{MISSING_SKILL_NAME}", json=skill_payload())
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_another_users_skill_is_not_found(
    skills_client: SkillsClient, seed_skill: SeedSkill, second_user: SecondUser
) -> None:
    name = seed_skill(owner=second_user)["name"]
    resp = skills_client.put(f"/{name}", json=skill_payload())
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("payload", "loc"),
    [
        pytest.param({"description": ""}, ["body", "description"], id="empty-description-missing-body"),
        pytest.param(skill_payload(allowed_tools="calculator"), ["body", "allowed_tools"], id="allowed-tools-not-a-list"),
    ],
)
def test_invalid_body_is_unprocessable(skills_client: SkillsClient, payload: dict[str, Any], loc: list[str]) -> None:
    # Pydantic rejects the body before the skill is looked up, so no seed is needed.
    with outside_request_contract("a body the spec does not allow"):
        resp = skills_client.put(f"/{MISSING_SKILL_NAME}", json=payload)
        assert resp.status_code == 422, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["detail"][0]["loc"] == loc
    assert_spec_forbids_request(resp, ROUTE)


def test_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.put(f"/{MISSING_SKILL_NAME}", json=skill_payload(), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_null_body_name_is_accepted(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    # `name` is `str | None` in the shared write model, and the path name wins on update.
    name = seed_skill()["name"]
    resp = skills_client.put(f"/{name}", json=skill_payload() | {"name": None})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["name"] == name
