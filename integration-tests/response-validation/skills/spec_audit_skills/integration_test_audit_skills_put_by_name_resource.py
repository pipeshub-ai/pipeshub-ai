"""Strict OpenAPI audit of PUT /api/v1/skills/:name/resource."""

from __future__ import annotations

from typing import Any

import pytest
from helper.second_user import SecondUser
from skills_audit_support import (
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
RESOURCE_CONTENT = "# Spec audit resource\n\nWritten by the skills spec audit.\n"
WRITE = {"path": RESOURCE_PATH, "content": RESOURCE_CONTENT}


def _listed_paths(skill: dict[str, Any]) -> list[str]:
    # GET /:name lists resources as {kind: [paths]}, never their content.
    return [path for paths in skill["resources"].values() for path in paths]


def test_put_writes_then_overwrites_resource(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]

    resp = skills_client.put(f"/{name}/resource", json=WRITE)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"status": "success"}

    # Same path again is an upsert, not a conflict; empty content is a valid file.
    again = skills_client.put(f"/{name}/resource", json={"path": RESOURCE_PATH, "content": ""})
    assert again.status_code == 200, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)

    stored = skills_client.fetch(name)
    assert stored.status_code == 200, stored.text[:500]
    assert _listed_paths(stored.json()) == [RESOURCE_PATH]


def test_unknown_body_field_is_ignored(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    name = seed_skill()["name"]
    with outside_request_contract("an undocumented body field, to show it is ignored"):
        resp = skills_client.put(f"/{name}/resource", json={**WRITE, "spec_audit_unknown": 1})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert _listed_paths(skills_client.fetch(name).json()) == [RESOURCE_PATH]


@pytest.mark.parametrize(
    ("path", "detail_part"),
    [
        pytest.param("../spec-audit-escape.md", "..", id="path_traversal"),
        pytest.param("/spec-audit/absolute.md", "must be relative", id="absolute_path"),
        pytest.param("SKILL.md", "SKILL.md", id="reserved_skill_md"),
    ],
)
def test_put_invalid_resource_path_is_bad_request(
    skills_client: SkillsClient, seed_skill: SeedSkill, path: str, detail_part: str
) -> None:
    name = seed_skill()["name"]

    resp = skills_client.put(f"/{name}/resource", json={"path": path, "content": RESOURCE_CONTENT})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert detail_part in resp.json()["detail"]

    stored = skills_client.fetch(name)
    assert stored.status_code == 200, stored.text[:500]
    assert _listed_paths(stored.json()) == []


@pytest.mark.parametrize(
    ("payload", "locs"),
    [
        pytest.param({"path": RESOURCE_PATH}, [["body", "content"]], id="content_missing"),
        pytest.param({}, [["body", "path"], ["body", "content"]], id="empty_object"),
        pytest.param({"path": "", "content": RESOURCE_CONTENT}, [["body", "path"]], id="path_empty"),
        pytest.param({"path": 5, "content": RESOURCE_CONTENT}, [["body", "path"]], id="path_not_text"),
        pytest.param({"path": RESOURCE_PATH, "content": 5}, [["body", "content"]], id="content_not_text"),
    ],
)
def test_invalid_body_is_unprocessable(
    skills_client: SkillsClient, payload: dict[str, Any], locs: list[list[str]]
) -> None:
    # Pydantic rejects the body before the skill is looked up, so no seed is needed.
    with outside_request_contract("a body the spec does not allow"):
        resp = skills_client.put(f"/{MISSING_SKILL_NAME}/resource", json=payload)
        assert resp.status_code == 422, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert [e["loc"] for e in resp.json()["detail"]] == locs
    assert_spec_forbids_request(resp, ROUTE)


def test_put_missing_skill_is_not_found(skills_client: SkillsClient) -> None:
    resp = skills_client.put(f"/{MISSING_SKILL_NAME}/resource", json=WRITE)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"detail": f"Skill {MISSING_SKILL_NAME!r} not found"}


def test_put_another_users_skill_is_not_found(
    skills_client: SkillsClient, second_user: SecondUser, seed_skill: SeedSkill
) -> None:
    name = seed_skill(owner=second_user)["name"]
    resp = skills_client.put(f"/{name}/resource", json=WRITE)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_put_builtin_skill_is_forbidden(skills_client: SkillsClient, builtin_skill_name: str) -> None:
    resp = skills_client.put(f"/{builtin_skill_name}/resource", json=WRITE)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "built-in" in resp.json()["detail"]


def test_put_unsafe_name_is_rejected_by_path_guard(skills_client: SkillsClient) -> None:
    resp = skills_client.put(f"/{UNSAFE_PATH_SEGMENT}/resource", json=WRITE)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_put_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.put(f"/{MISSING_SKILL_NAME}/resource", auth=False, json=WRITE)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
