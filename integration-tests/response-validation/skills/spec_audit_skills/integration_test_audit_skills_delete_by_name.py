"""Strict OpenAPI audit of DELETE /api/v1/skills/:name."""

from __future__ import annotations

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

ROUTE = "/api/v1/skills/:name"


def test_delete_removes_skill_then_reports_not_found(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]

    resp = skills_client.remove(name)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"status": "success"}

    again = skills_client.remove(name)
    assert again.status_code == 404, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)
    assert "not found" in again.json()["detail"].lower()


@pytest.mark.parametrize("detach", ["true", "false"])
def test_delete_with_explicit_detach(skills_client: SkillsClient, seed_skill: SeedSkill, detach: str) -> None:
    name = seed_skill()["name"]
    resp = skills_client.remove(name, detach=detach)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert skills_client.fetch(name).status_code == 404


@pytest.mark.parametrize("detach", ["false", "true"])
def test_delete_skill_another_skill_requires_is_a_conflict(
    skills_client: SkillsClient, seed_skill: SeedSkill, detach: str
) -> None:
    # A `requires` dependency can never be detached, so detach=true is refused as well.
    required = seed_skill()["name"]
    dependent = seed_skill(requires=[required])["name"]

    resp = skills_client.remove(required, detach=detach)
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    detail = resp.json()["detail"]
    assert detail["usedByAgents"] == []
    assert detail["requiredBySkills"] == [dependent]
    assert skills_client.fetch(required).status_code == 200


def test_detach_accepts_other_boolean_spellings(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    # FastAPI reads yes/no, on/off and 1/0 as booleans; the spec documents only true/false.
    name = seed_skill()["name"]
    with outside_request_contract("a boolean spelling the spec does not list, to show it is tolerated"):
        resp = skills_client.remove(name, detach="yes")
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("detach", ["not-a-bool", ""], ids=["word", "empty"])
def test_delete_non_boolean_detach_is_unprocessable(skills_client: SkillsClient, detach: str) -> None:
    # FastAPI validates the query before the handler runs, so no skill needs to exist.
    with outside_request_contract("a detach value the spec does not allow"):
        resp = skills_client.remove(MISSING_SKILL_NAME, detach=detach)
        assert resp.status_code == 422, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["detail"][0]["loc"] == ["query", "detach"]
    assert_spec_forbids_request(resp, ROUTE)


def test_member_deleting_admins_skill_is_not_found(
    skills_client: SkillsClient, second_user: SecondUser, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]
    resp = request_as(second_user, "DELETE", f"/{name}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert skills_client.fetch(name).status_code == 200


def test_delete_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.remove(MISSING_SKILL_NAME, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_builtin_skill_is_forbidden(
    skills_client: SkillsClient, builtin_skill_name: str
) -> None:
    # The built-in check runs before the delete, so even detach=true cannot remove one.
    resp = skills_client.remove(builtin_skill_name, detach="true")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "built-in" in resp.json()["detail"]


def test_delete_unsafe_name_is_rejected_by_path_guard(skills_client: SkillsClient) -> None:
    resp = skills_client.remove(UNSAFE_PATH_SEGMENT)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
