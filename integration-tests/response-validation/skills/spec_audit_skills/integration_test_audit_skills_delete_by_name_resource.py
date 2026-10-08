"""Strict OpenAPI audit of DELETE /api/v1/skills/:name/resource."""

from __future__ import annotations

from typing import Any

import pytest
from helper.second_user import SecondUser
from skills_audit_support import (
    MISSING_RESOURCE_PATH,
    MISSING_SKILL_NAME,
    RESOURCE_PATH,
    UNSAFE_PATH_SEGMENT,
    SeedSkill,
    SkillsClient,
)
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/:name/resource"


def _seed_resource(skills_client: SkillsClient, name: str) -> None:
    written = skills_client.put(
        f"/{name}/resource", json={"path": RESOURCE_PATH, "content": "spec audit resource\n"}
    )
    assert written.status_code == 200, f"seeding a resource failed: {written.text[:500]}"


def test_delete_removes_resource_then_reports_not_found(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]
    _seed_resource(skills_client, name)

    resp = skills_client.delete(f"/{name}/resource", params={"path": RESOURCE_PATH})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"status": "success"}
    assert not any(skills_client.fetch(name).json()["resources"].values())

    again = skills_client.delete(f"/{name}/resource", params={"path": RESOURCE_PATH})
    assert again.status_code == 404, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)


def test_unknown_query_parameter_is_ignored(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    name = seed_skill()["name"]
    _seed_resource(skills_client, name)
    with outside_request_contract("an undocumented query parameter, to show it is ignored"):
        resp = skills_client.delete(
            f"/{name}/resource", params={"path": RESOURCE_PATH, "spec_audit_unknown": "1"}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("path", [MISSING_RESOURCE_PATH, "../spec-audit-escape.md"], ids=["unknown", "traversal"])
def test_delete_unknown_resource_of_existing_skill_is_not_found(
    skills_client: SkillsClient, seed_skill: SeedSkill, path: str
) -> None:
    name = seed_skill()["name"]

    resp = skills_client.delete(f"/{name}/resource", params={"path": path})
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"detail": f"Resource {path!r} not found for skill {name!r}."}

    stored = skills_client.fetch(name)
    assert stored.status_code == 200, stored.text[:500]


@pytest.mark.parametrize("whose", ["missing", "another-user"])
def test_delete_resource_of_invisible_skill_is_not_found(
    skills_client: SkillsClient, seed_skill: SeedSkill, second_user: SecondUser, whose: str
) -> None:
    # Unlike GET, the built-in guard loads the skill first, so this is a missing skill.
    name = MISSING_SKILL_NAME if whose == "missing" else seed_skill(owner=second_user)["name"]
    resp = skills_client.delete(f"/{name}/resource", params={"path": RESOURCE_PATH})
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"detail": f"Skill {name!r} not found"}


def test_delete_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.delete(
        f"/{MISSING_SKILL_NAME}/resource", auth=False, params={"path": RESOURCE_PATH}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("params", "error_type"),
    [
        pytest.param({}, "missing", id="path_missing"),
        pytest.param({"path": ""}, "string_too_short", id="path_empty"),
        pytest.param([("path", RESOURCE_PATH), ("path", MISSING_RESOURCE_PATH)], "missing", id="path_repeated"),
    ],
)
def test_delete_invalid_path_query_is_unprocessable(
    skills_client: SkillsClient, params: Any, error_type: str
) -> None:
    # FastAPI validates the query before the handler runs, so no skill needs to exist.
    with outside_request_contract("a path query the spec does not allow"):
        resp = skills_client.delete(f"/{MISSING_SKILL_NAME}/resource", params=params)
        assert resp.status_code == 422, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["detail"][0]
    assert (error["loc"], error["type"]) == (["query", "path"], error_type)
    assert_spec_forbids_request(resp, ROUTE)


def test_delete_resource_of_builtin_skill_is_forbidden(
    skills_client: SkillsClient, builtin_skill_name: str
) -> None:
    # The built-in guard runs before the resource lookup, so the path need not exist.
    resp = skills_client.delete(
        f"/{builtin_skill_name}/resource", params={"path": MISSING_RESOURCE_PATH}
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "built-in" in resp.json()["detail"]


def test_delete_unsafe_name_is_rejected_by_path_guard(skills_client: SkillsClient) -> None:
    resp = skills_client.delete(f"/{UNSAFE_PATH_SEGMENT}/resource", params={"path": RESOURCE_PATH})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
