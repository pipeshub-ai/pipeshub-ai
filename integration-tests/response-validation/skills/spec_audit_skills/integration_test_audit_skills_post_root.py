"""Strict OpenAPI audit of POST /api/v1/skills."""

from __future__ import annotations

from typing import Any

import pytest
from helper.second_user import SecondUser
from skills_audit_support import (
    SeedSkill,
    SkillsClient,
    request_as,
    skill_payload,
    unique_skill_name,
)
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills"


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
        related=["spec-audit-related"],
        requires=[],
        concepts=["auditing"],
    )

    try:
        resp = skills_client.create(payload)
        assert resp.status_code == 201, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        created = resp.json()
        assert created["name"] == name
        assert created["description"] == payload["description"]
        assert created["status"] == "active"
        assert created["source"] == "manual"
        assert created["category"] == "spec-audit"
        assert created["tags"] == ["spec-audit"]
        assert created["allowedTools"] == ["calculator"]
        # The store assigns the timestamps after the response is built.
        assert created["createdAt"] is None and created["updatedAt"] is None

        again = skills_client.create(payload)
        assert again.status_code == 409, again.text[:500]
        assert_strict_openapi_exchange(again, ROUTE)
        assert again.json() == {"detail": f"Skill {name!r} already exists"}
    finally:
        skills_client.remove(name, detach="true")


def test_create_ignores_unknown_body_field(skills_client: SkillsClient) -> None:
    name = unique_skill_name()
    try:
        with outside_request_contract("an undocumented body field, to show it is ignored"):
            resp = skills_client.create(skill_payload(name, spec_audit_unknown=1))
            assert resp.status_code == 201, resp.text[:500]
            assert_strict_openapi_exchange(resp, ROUTE)
    finally:
        skills_client.remove(name, detach="true")


def test_create_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.create(skill_payload(unique_skill_name()), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_without_name_is_bad_request(skills_client: SkillsClient) -> None:
    # `name` is optional in the shared write model; the create handler rejects it itself.
    resp = skills_client.create(skill_payload())
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"detail": "'name' is required to create a skill."}
    assert_spec_forbids_request(resp, ROUTE)


def test_create_with_null_name_is_bad_request(skills_client: SkillsClient) -> None:
    # The shared write model accepts `name: null`; only the create handler refuses it.
    resp = skills_client.create(skill_payload() | {"name": None})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"detail": "'name' is required to create a skill."}


@pytest.mark.parametrize(
    ("overrides", "detail_part"),
    [
        pytest.param({"name": "Spec_Audit_Bad_Name"}, "must be lowercase alphanumeric", id="name-not-kebab-case"),
        pytest.param({"description": "d" * 1025}, "1024", id="description-too-long"),
    ],
)
def test_create_invalid_skill_md_is_bad_request(
    skills_client: SkillsClient, overrides: dict[str, Any], detail_part: str
) -> None:
    payload = {**skill_payload(unique_skill_name()), **overrides}
    resp = skills_client.create(payload)
    try:
        assert resp.status_code == 400, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert detail_part in resp.json()["detail"]
    finally:
        if resp.status_code == 201:
            skills_client.remove(payload["name"], detach="true")


@pytest.mark.parametrize(
    ("payload", "loc"),
    [
        pytest.param({"name": "spec-audit-no-description", "body": "# x"}, ["body", "description"], id="missing-description"),
        pytest.param(skill_payload("spec-audit-empty-body", body=""), ["body", "body"], id="empty-body"),
        pytest.param(skill_payload("spec-audit-tags-text", tags="spec-audit"), ["body", "tags"], id="tags-not-a-list"),
        pytest.param(skill_payload("spec-audit-desc-number", description=5), ["body", "description"], id="description-not-text"),
        pytest.param([skill_payload("spec-audit-array")], ["body"], id="body-not-an-object"),
    ],
)
def test_create_invalid_body_is_unprocessable(
    skills_client: SkillsClient, payload: Any, loc: list[str]
) -> None:
    with outside_request_contract("a body the spec does not allow"):
        resp = skills_client.create(payload)
        assert resp.status_code == 422, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["detail"][0]["loc"] == loc
    assert_spec_forbids_request(resp, ROUTE)


def test_create_under_builtin_name_is_conflict(
    skills_client: SkillsClient, builtin_skill_name: str
) -> None:
    resp = skills_client.create(skill_payload(builtin_skill_name))
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"detail": f"{builtin_skill_name!r} is a built-in skill name."}


def test_create_over_another_users_skill_takes_it_over(
    skills_client: SkillsClient, seed_skill: SeedSkill, second_user: SecondUser
) -> None:
    # API bug: the duplicate check sees only the caller's skills, but the stored key is
    # org-wide, so this overwrites the member's skill and makes the admin its owner.
    name = seed_skill(owner=second_user, description="Owned by the member")["name"]
    try:
        resp = skills_client.create(skill_payload(name, description="Written by the admin"))
        assert resp.status_code == 201, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert request_as(second_user, "GET", f"/{name}").status_code == 404
        assert skills_client.fetch(name).json()["description"] == "Written by the admin"
    finally:
        skills_client.remove(name, detach="true")
