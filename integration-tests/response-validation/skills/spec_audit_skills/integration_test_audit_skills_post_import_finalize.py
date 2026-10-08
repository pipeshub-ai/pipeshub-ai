"""Strict OpenAPI audit of POST /api/v1/skills/import/finalize.

The import routes share a 10 calls/minute limiter per user that counts refused requests
too, so most calls run as a disposable member and the admin makes four.
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser, create_second_user, delete_second_user
from skills_audit_support import (
    RESOURCE_PATH,
    SeedSkill,
    SkillsClient,
    request_as,
    skill_md,
    unique_skill_name,
)
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/import/finalize"
FINALIZE = "/import/finalize"


def test_finalize_creates_skill_with_resources(skills_client: SkillsClient) -> None:
    name = unique_skill_name()
    try:
        resp = skills_client.post(
            FINALIZE,
            json={
                "content": skill_md(name),
                "resources": {RESOURCE_PATH: "# Spec audit reference\n"},
                "category": "spec-audit",
                "subcategory": "imported",
            },
        )
        assert resp.status_code == 201, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        created = resp.json()
        assert created["name"] == name
        assert created["category"] == "spec-audit"
        assert skills_client.fetch(name).json()["resources"] == {"references": [RESOURCE_PATH]}
    finally:
        skills_client.remove(name, detach="true")


def test_finalize_name_overrides_the_frontmatter_name(import_user: SecondUser) -> None:
    original, renamed = unique_skill_name(), unique_skill_name()
    try:
        resp = request_as(import_user, "POST", FINALIZE, json={"content": skill_md(original), "name": renamed})
        assert resp.status_code == 201, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert resp.json()["name"] == renamed
    finally:
        request_as(import_user, "DELETE", f"/{renamed}", params={"detach": "true"})


def test_finalize_ignores_unknown_body_field(import_user: SecondUser) -> None:
    name = unique_skill_name()
    try:
        with outside_request_contract("an undocumented body field, to show it is ignored"):
            resp = request_as(
                import_user, "POST", FINALIZE, json={"content": skill_md(name), "spec_audit_unknown": 1}
            )
            assert resp.status_code == 201, resp.text[:500]
            assert_strict_openapi_exchange(resp, ROUTE)
    finally:
        request_as(import_user, "DELETE", f"/{name}", params={"detach": "true"})


def test_finalize_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.post(FINALIZE, auth=False, json={"content": skill_md(unique_skill_name())})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("body", "loc"),
    [
        pytest.param({"resources": {}}, ["body", "content"], id="missing-content"),
        pytest.param({"content": "x", "resources": {"a.md": 1}}, ["body", "resources", "a.md"], id="resource-not-text"),
    ],
)
def test_finalize_invalid_body_is_unprocessable(
    import_user: SecondUser, body: dict[str, Any], loc: list[str]
) -> None:
    # Node has no validator here: the pydantic 422 from Python passes through.
    with outside_request_contract("a body the spec does not allow"):
        resp = request_as(import_user, "POST", FINALIZE, json=body)
        assert resp.status_code == 422, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["detail"][0]["loc"] == loc
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    ("content", "detail_start"),
    [
        pytest.param("# No frontmatter here\n", "Imported SKILL.md is missing a 'name' field.", id="no-name"),
        pytest.param("---\n- a\n---\nbody\n", "Could not read 'name' from the imported SKILL.md", id="frontmatter-not-a-mapping"),
        pytest.param(skill_md("Spec_Audit_Bad_Name"), "Skill name 'Spec_Audit_Bad_Name' must be lowercase", id="bad-name"),
    ],
)
def test_finalize_unreadable_skill_md_is_bad_request(
    import_user: SecondUser, content: str, detail_start: str
) -> None:
    resp = request_as(import_user, "POST", FINALIZE, json={"content": content})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["detail"].startswith(detail_start), resp.json()


def test_finalize_existing_name_is_conflict(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    name = seed_skill()["name"]

    resp = skills_client.post(FINALIZE, json={"content": skill_md(name)})
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"detail": f"Skill {name!r} already exists"}


def test_finalize_builtin_name_is_conflict(import_user: SecondUser, builtin_skill_name: str) -> None:
    resp = request_as(import_user, "POST", FINALIZE, json={"content": skill_md(builtin_skill_name)})
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"detail": f"{builtin_skill_name!r} is a built-in skill name."}


def test_finalize_over_another_users_skill_takes_it_over(
    skills_client: SkillsClient, seed_skill: SeedSkill, second_user: SecondUser
) -> None:
    # API bug: the duplicate check sees only the caller's skills, but the stored key is
    # org-wide, so this overwrites the member's skill and makes the admin its owner.
    name = seed_skill(owner=second_user)["name"]
    try:
        resp = skills_client.post(FINALIZE, json={"content": skill_md(name)})
        assert resp.status_code == 201, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert request_as(second_user, "GET", f"/{name}").status_code == 404
        assert skills_client.fetch(name).status_code == 200
    finally:
        skills_client.remove(name, detach="true")


@pytest.mark.parametrize(
    ("path", "detail"),
    [
        pytest.param("../escape.md", "Resource path '../escape.md' must not contain '..' traversal segments", id="traversal"),
        pytest.param("/abs.md", "Resource path '/abs.md' must be relative, not absolute", id="absolute"),
        pytest.param("SKILL.md", "Resource path must not be 'SKILL.md'", id="skill-md"),
    ],
)
def test_finalize_invalid_resource_path_is_refused_after_the_skill_is_created(
    pipeshub_client: PipeshubClient, path: str, detail: str
) -> None:
    # API bug: the handler creates the skill before it writes (and validates) the resources.
    # A fresh member per case keeps these calls off the shared import rate-limit budgets.
    user = create_second_user(pipeshub_client)
    name = unique_skill_name()
    try:
        resp = request_as(user, "POST", FINALIZE, json={"content": skill_md(name), "resources": {path: "x"}})
        assert resp.status_code == 400, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert resp.json()["detail"].startswith(detail), resp.json()

        stored = request_as(user, "GET", f"/{name}")
        assert stored.status_code == 200, stored.text[:500]
        assert not any(stored.json()["resources"].values())

        retry = request_as(user, "POST", FINALIZE, json={"content": skill_md(name)})
        assert retry.status_code == 409, retry.text[:500]
        assert_strict_openapi_exchange(retry, ROUTE)
    finally:
        request_as(user, "DELETE", f"/{name}", params={"detach": "true"})
        delete_second_user(pipeshub_client, user)
