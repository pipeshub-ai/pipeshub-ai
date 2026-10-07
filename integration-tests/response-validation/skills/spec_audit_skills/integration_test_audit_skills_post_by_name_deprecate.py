"""Strict OpenAPI audit of POST /api/v1/skills/:name/deprecate."""

from __future__ import annotations

from typing import Any

import pytest
from helper.second_user import SecondUser
from skills_audit_support import (
    MISSING_SKILL_NAME,
    UNSAFE_PATH_SEGMENT,
    SeedSkill,
    SkillsClient,
    request_as,
)
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/:name/deprecate"

DEPRECATE_BODY = {"reason": "Superseded during the spec audit", "replaced_by": MISSING_SKILL_NAME}


def _deprecate_path(name: str) -> str:
    return f"/{name}/deprecate"


def test_deprecate_marks_skill_deprecated(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]

    resp = skills_client.post(_deprecate_path(name), json=DEPRECATE_BODY)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"status": "success"}

    stored = skills_client.fetch(name)
    assert stored.status_code == 200, stored.text[:500]
    metadata = stored.json()
    assert metadata["status"] == "deprecated"
    assert metadata["deprecatedReason"] == DEPRECATE_BODY["reason"]
    # replaced_by is not checked against existing skills.
    assert metadata["replacedBy"] == DEPRECATE_BODY["replaced_by"]


@pytest.mark.parametrize("replaced_by", [None, "absent"], ids=["null", "omitted"])
def test_replaced_by_is_optional(
    skills_client: SkillsClient, seed_skill: SeedSkill, replaced_by: str | None
) -> None:
    name = seed_skill()["name"]
    payload: dict[str, Any] = {"reason": "Spec audit"}
    if replaced_by is None:
        payload["replaced_by"] = None

    resp = skills_client.post(_deprecate_path(name), json=payload)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert skills_client.fetch(name).json()["replacedBy"] is None


def test_deprecate_a_disabled_skill_is_allowed(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    name = seed_skill()["name"]
    assert skills_client.post(f"/{name}/disable").status_code == 200

    resp = skills_client.post(_deprecate_path(name), json={"reason": "Spec audit"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert skills_client.fetch(name).json()["status"] == "deprecated"


def test_unknown_body_field_is_ignored(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    name = seed_skill()["name"]
    with outside_request_contract("an undocumented body field, to show it is ignored"):
        resp = skills_client.post(_deprecate_path(name), json={"reason": "Spec audit", "spec_audit_unknown": 1})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("payload", "loc"),
    [
        pytest.param({"replaced_by": MISSING_SKILL_NAME}, ["body", "reason"], id="reason_missing"),
        pytest.param({"reason": ""}, ["body", "reason"], id="reason_empty"),
        pytest.param({"reason": 5}, ["body", "reason"], id="reason_not_text"),
        pytest.param({"reason": "Spec audit", "replaced_by": 5}, ["body", "replaced_by"], id="replaced_by_not_text"),
    ],
)
def test_invalid_body_is_unprocessable(
    skills_client: SkillsClient, seed_skill: SeedSkill, payload: dict[str, Any], loc: list[str]
) -> None:
    name = seed_skill()["name"]

    with outside_request_contract("a body the spec does not allow"):
        resp = skills_client.post(_deprecate_path(name), json=payload)
        assert resp.status_code == 422, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["detail"][0]["loc"] == loc
    assert_spec_forbids_request(resp, ROUTE)

    stored = skills_client.fetch(name)
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json()["status"] == "active"


def test_deprecate_missing_skill_is_not_found(skills_client: SkillsClient) -> None:
    resp = skills_client.post(_deprecate_path(MISSING_SKILL_NAME), json=DEPRECATE_BODY)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_deprecating_admins_skill_is_not_found(
    skills_client: SkillsClient, second_user: SecondUser, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]

    # Custom skills are creator-scoped: a non-owner cannot see the skill, so no 403.
    resp = request_as(second_user, "POST", _deprecate_path(name), json=DEPRECATE_BODY)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    stored = skills_client.fetch(name)
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json()["status"] == "active"


def test_deprecate_builtin_skill_is_forbidden(skills_client: SkillsClient, builtin_skill_name: str) -> None:
    resp = skills_client.post(_deprecate_path(builtin_skill_name), json=DEPRECATE_BODY)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "built-in" in resp.json()["detail"]


def test_deprecate_unsafe_name_is_rejected(skills_client: SkillsClient) -> None:
    resp = skills_client.post(_deprecate_path(UNSAFE_PATH_SEGMENT), json=DEPRECATE_BODY)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_deprecate_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.post(_deprecate_path(MISSING_SKILL_NAME), json=DEPRECATE_BODY, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
