"""Strict OpenAPI audit of GET /api/v1/skills/:name/versions."""

from __future__ import annotations

import pytest
from skills_audit_support import (
    MISSING_SKILL_NAME,
    UNSAFE_PATH_SEGMENT,
    SeedSkill,
    SkillsClient,
    skill_payload,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/:name/versions"


def test_versions_lists_the_revision_archived_by_an_update(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    seeded = seed_skill()
    name = seeded["name"]
    # History is written only when a revision is overwritten, so a fresh skill has none.
    updated = skills_client.put(
        f"/{name}", json=skill_payload(description="Updated by the skills spec audit")
    )
    assert updated.status_code == 200, updated.text[:500]

    resp = skills_client.get(f"/{name}/versions")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    versions = resp.json()["versions"]
    assert len(versions) == 1, versions
    assert set(versions[0]) == {"version", "updatedBy", "createdAt", "summary"}
    assert versions[0]["version"] == seeded["version"]


def test_versions_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.get(f"/{MISSING_SKILL_NAME}/versions", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_versions_of_missing_skill_is_an_empty_list(skills_client: SkillsClient) -> None:
    # The store answers [] for an unknown skill; the route never raises 404.
    resp = skills_client.get(f"/{MISSING_SKILL_NAME}/versions")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"versions": []}


def test_versions_of_another_users_skill_is_an_empty_list(
    skills_client: SkillsClient, second_user: SecondUser, seed_skill: SeedSkill
) -> None:
    # Custom skills are creator-scoped: an invisible skill looks exactly like a missing one.
    name = seed_skill(owner=second_user)["name"]

    resp = skills_client.get(f"/{name}/versions")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"versions": []}


def test_versions_unsafe_name_is_rejected_by_path_guard(skills_client: SkillsClient) -> None:
    resp = skills_client.get(f"/{UNSAFE_PATH_SEGMENT}/versions")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_versions_are_listed_newest_first(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    seeded = seed_skill()
    name = seeded["name"]
    first = skills_client.put(f"/{name}", json=skill_payload(description="Spec audit revision two"))
    assert first.status_code == 200, first.text[:500]
    second = skills_client.put(f"/{name}", json=skill_payload(description="Spec audit revision three"))
    assert second.status_code == 200, second.text[:500]

    resp = skills_client.get(f"/{name}/versions")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert [v["version"] for v in resp.json()["versions"]] == [first.json()["version"], seeded["version"]]


def test_query_parameter_is_ignored(skills_client: SkillsClient) -> None:
    with outside_request_contract("an undocumented query parameter, to show it is ignored"):
        resp = skills_client.get(f"/{MISSING_SKILL_NAME}/versions", params={"spec_audit_unknown": "1"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
