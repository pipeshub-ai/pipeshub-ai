"""Strict OpenAPI audit of POST /api/v1/skills/:name/rollback."""

from __future__ import annotations

from typing import Any

import pytest
from helper.second_user import SecondUser
from skills_audit_support import (
    MISSING_SKILL_NAME,
    MISSING_VERSION,
    UNSAFE_PATH_SEGMENT,
    SeedSkill,
    SkillsClient,
    request_as,
    skill_payload,
)
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/:name/rollback"

ORIGINAL_DESCRIPTION = "Spec audit rollback: original revision"
EDITED_DESCRIPTION = "Spec audit rollback: edited revision"


def test_rollback_restores_archived_version_as_new_revision(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    seeded = seed_skill(description=ORIGINAL_DESCRIPTION)
    name, original_version = seeded["name"], seeded["version"]

    # A version only becomes a rollback target once an update has archived it.
    edited = skills_client.put(f"/{name}", json=skill_payload(description=EDITED_DESCRIPTION))
    assert edited.status_code == 200, edited.text[:500]
    edited_version = edited.json()["version"]

    resp = skills_client.post(f"/{name}/rollback", json={"version": original_version})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["name"] == name
    assert body["description"] == ORIGINAL_DESCRIPTION
    # The restored copy gets a fresh version number; history never rewinds.
    assert body["version"] not in (original_version, edited_version)

    unknown = skills_client.post(f"/{name}/rollback", json={"version": MISSING_VERSION})
    assert unknown.status_code == 404, unknown.text[:500]
    assert_strict_openapi_exchange(unknown, ROUTE)
    assert MISSING_VERSION in unknown.json()["detail"]


def test_rollback_to_current_version_is_not_found(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    # The live revision is not in the history until it is overwritten.
    seeded = seed_skill()
    resp = skills_client.post(f"/{seeded['name']}/rollback", json={"version": seeded["version"]})
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_body_field_is_ignored(skills_client: SkillsClient, seed_skill: SeedSkill) -> None:
    seeded = seed_skill()
    name = seeded["name"]
    assert skills_client.put(f"/{name}", json=skill_payload(description=EDITED_DESCRIPTION)).status_code == 200
    with outside_request_contract("an undocumented body field, to show it is ignored"):
        resp = skills_client.post(
            f"/{name}/rollback", json={"version": seeded["version"], "spec_audit_unknown": 1}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_rollback_missing_skill_is_not_found(skills_client: SkillsClient) -> None:
    resp = skills_client.post(f"/{MISSING_SKILL_NAME}/rollback", json={"version": "1.0.0"})
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_rollback_of_admins_skill_is_not_found(
    second_user: SecondUser, seed_skill: SeedSkill
) -> None:
    seeded = seed_skill()
    resp = request_as(second_user, "POST", f"/{seeded['name']}/rollback", json={"version": seeded["version"]})
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="version_missing"),
        pytest.param({"version": ""}, id="version_empty"),
        pytest.param({"version": 1}, id="version_not_text"),
    ],
)
def test_invalid_body_is_unprocessable(skills_client: SkillsClient, payload: dict[str, Any]) -> None:
    # Pydantic rejects the body before the skill is looked up, so no seed is needed.
    with outside_request_contract("a body the spec does not allow"):
        resp = skills_client.post(f"/{MISSING_SKILL_NAME}/rollback", json=payload)
        assert resp.status_code == 422, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["detail"][0]["loc"] == ["body", "version"]
    assert_spec_forbids_request(resp, ROUTE)


def test_rollback_builtin_skill_is_forbidden(
    skills_client: SkillsClient, builtin_skill_name: str
) -> None:
    # Refused before the store is touched, so the org-wide built-in stays as it is.
    resp = skills_client.post(f"/{builtin_skill_name}/rollback", json={"version": "1.0.0"})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "built-in skill" in resp.json()["detail"]


def test_rollback_unsafe_name_is_rejected_by_path_guard(skills_client: SkillsClient) -> None:
    resp = skills_client.post(f"/{UNSAFE_PATH_SEGMENT}/rollback", json={"version": "1.0.0"})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_rollback_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.post(
        f"/{MISSING_SKILL_NAME}/rollback", json={"version": "1.0.0"}, auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
