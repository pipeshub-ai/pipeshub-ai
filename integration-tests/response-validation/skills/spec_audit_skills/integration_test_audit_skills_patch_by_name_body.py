"""Strict OpenAPI audit of PATCH /api/v1/skills/:name/body."""

from __future__ import annotations

from typing import Any

import pytest
from skills_audit_support import (
    MISSING_SKILL_NAME,
    SKILL_BODY_MARKER,
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

ROUTE = "/api/v1/skills/:name/body"
REPLACEMENT = "spec-audit-patched"
STALE_IF_MATCH = "1"
PATCH = {"old_string": SKILL_BODY_MARKER, "new_string": REPLACEMENT}


def test_patch_replaces_marker_with_current_if_match(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]
    # The create response carries null timestamps, so the If-Match token comes from a read.
    current = skills_client.fetch(name)
    assert current.status_code == 200, f"reading the seeded skill failed: {current.text[:500]}"

    resp = skills_client.patch(f"/{name}/body", json=PATCH, headers={"If-Match": current.json()["updatedAt"]})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"status": "success"}

    stored = skills_client.fetch(name)
    assert stored.status_code == 200, stored.text[:500]
    body = stored.json()["body"]
    assert REPLACEMENT in body
    assert SKILL_BODY_MARKER not in body


def test_patch_archives_the_previous_revision(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    seeded = seed_skill()
    resp = skills_client.patch(f"/{seeded['name']}/body", json=PATCH)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    versions = skills_client.get(f"/{seeded['name']}/versions").json()["versions"]
    assert [v["version"] for v in versions] == [seeded["version"]]


def test_unknown_body_field_is_ignored(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    name = seed_skill()["name"]
    with outside_request_contract("an undocumented body field, to show it is ignored"):
        resp = skills_client.patch(f"/{name}/body", json={**PATCH, "spec_audit_unknown": 1})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert REPLACEMENT in skills_client.fetch(name).json()["body"]


@pytest.mark.parametrize(
    ("payload", "headers", "expected_status"),
    [
        pytest.param({"old_string": "spec-audit-absent-text", "new_string": REPLACEMENT}, {}, 400, id="old_string_not_in_body"),
        pytest.param({"old_string": "", "new_string": REPLACEMENT}, {}, 400, id="empty_old_string"),
        pytest.param(PATCH, {"If-Match": STALE_IF_MATCH}, 409, id="stale_if_match"),
        pytest.param(PATCH, {"If-Match": "spec-audit"}, 400, id="non_numeric_if_match"),
    ],
)
def test_patch_refused_on_existing_skill(
    skills_client: SkillsClient,
    seed_skill: SeedSkill,
    payload: dict[str, Any],
    headers: dict[str, str],
    expected_status: int,
) -> None:
    name = seed_skill()["name"]

    resp = skills_client.patch(f"/{name}/body", json=payload, headers=headers)
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "detail" in resp.json()

    stored = skills_client.fetch(name)
    assert stored.status_code == 200, stored.text[:500]
    assert SKILL_BODY_MARKER in stored.json()["body"]


@pytest.mark.parametrize(
    ("payload", "locs"),
    [
        pytest.param({"old_string": SKILL_BODY_MARKER}, [["body", "new_string"]], id="new_string_missing"),
        pytest.param({}, [["body", "old_string"], ["body", "new_string"]], id="empty_object"),
        pytest.param({"old_string": 1, "new_string": REPLACEMENT}, [["body", "old_string"]], id="old_string_not_text"),
    ],
)
def test_invalid_body_is_unprocessable(
    skills_client: SkillsClient, payload: dict[str, Any], locs: list[list[str]]
) -> None:
    # Pydantic rejects the body before the skill is looked up, so no seed is needed.
    with outside_request_contract("a body the spec does not allow"):
        resp = skills_client.patch(f"/{MISSING_SKILL_NAME}/body", json=payload)
        assert resp.status_code == 422, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert [e["loc"] for e in resp.json()["detail"]] == locs
    assert_spec_forbids_request(resp, ROUTE)


def test_patch_missing_skill_is_not_found(skills_client: SkillsClient) -> None:
    # The built-in guard loads the skill first, so a missing name never reaches the 400 branch.
    resp = skills_client.patch(f"/{MISSING_SKILL_NAME}/body", json=PATCH)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_patch_builtin_skill_is_forbidden(skills_client: SkillsClient, builtin_skill_name: str) -> None:
    resp = skills_client.patch(f"/{builtin_skill_name}/body", json=PATCH)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"detail": f"{builtin_skill_name!r} is a built-in skill and cannot be modified."}


def test_patch_unsafe_name_is_rejected_by_path_guard(skills_client: SkillsClient) -> None:
    resp = skills_client.patch(f"/{UNSAFE_PATH_SEGMENT}/body", json=PATCH)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_patch_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.patch(f"/{MISSING_SKILL_NAME}/body", json=PATCH, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
