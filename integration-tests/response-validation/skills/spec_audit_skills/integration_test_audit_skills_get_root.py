"""Strict OpenAPI audit of GET /api/v1/skills."""

from __future__ import annotations

import uuid

import pytest
from helper.second_user import SecondUser
from skills_audit_support import SeedSkill, SkillsClient
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills"


def _unique_category() -> str:
    return f"spec-audit-cat-{uuid.uuid4().hex[:10]}"


def test_list_filtered_by_category_returns_own_skill(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    category = _unique_category()
    name = seed_skill(category=category, subcategory="listed", tags=["spec-audit"])["name"]

    resp = skills_client.list(
        category=category, subcategory="listed", status="active", source="manual", tag="spec-audit"
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    skills = resp.json()["skills"]
    assert [skill["name"] for skill in skills] == [name]
    assert skills[0]["category"] == category
    # Unlike the create response, a read carries the timestamps the store assigned.
    assert isinstance(skills[0]["createdAt"], str)


def test_list_free_text_query_matches_name(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    category = _unique_category()
    name = seed_skill(category=category)["name"]

    resp = skills_client.list(category=category, q=name)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert [skill["name"] for skill in resp.json()["skills"]] == [name]


def test_list_includes_builtin_skills(skills_client: SkillsClient) -> None:
    resp = skills_client.list(source="builtin")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    skills = resp.json()["skills"]
    assert skills, "GET / seeds the built-in catalog, so it is never empty"
    assert all(skill["source"] == "builtin" for skill in skills)


def test_list_does_not_return_another_users_skill(
    skills_client: SkillsClient, seed_skill: SeedSkill, second_user: SecondUser
) -> None:
    category = _unique_category()
    seed_skill(owner=second_user, category=category)

    resp = skills_client.list(category=category)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"skills": []}


def test_list_ignores_unknown_query_parameter(skills_client: SkillsClient) -> None:
    category = _unique_category()
    with outside_request_contract("an undocumented query parameter, to show it is ignored"):
        resp = skills_client.list(category=category, spec_audit_unknown="1")
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"skills": []}


@pytest.mark.parametrize(
    ("parameter", "value"),
    [
        *[("status", v) for v in ("active", "draft", "deprecated", "candidate", "disabled")],
        *[("source", v) for v in ("manual", "agent_created", "imported", "builtin")],
    ],
)
def test_list_accepts_every_status_and_source(
    skills_client: SkillsClient, parameter: str, value: str
) -> None:
    resp = skills_client.list(category=_unique_category(), **{parameter: value})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"skills": []}


@pytest.mark.parametrize(
    ("parameter", "value"),
    [("status", "spec-audit-not-a-value"), ("source", "spec-audit-not-a-value"), ("source", "learned")],
)
def test_list_value_outside_enum_is_an_internal_error(
    skills_client: SkillsClient, parameter: str, value: str
) -> None:
    # API bug: the filter is converted to its enum inside the handler, so a bad value is a 500.
    with outside_request_contract("a filter value outside the documented enum"):
        resp = skills_client.list(**{parameter: value})
        assert resp.status_code == 500, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"detail": "Internal server error"}
    assert_spec_forbids_request(resp, ROUTE)


def test_list_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.list(auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
