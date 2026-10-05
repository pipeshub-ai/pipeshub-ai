"""Strict OpenAPI audit of PATCH /api/v1/skills/:name/body."""

from __future__ import annotations

from typing import Any

import pytest
from skills_audit_support import (
    MISSING_SKILL_NAME,
    SKILL_BODY_MARKER,
    SeedSkill,
    SkillsClient,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/:name/body"
REPLACEMENT = "spec-audit-patched"
STALE_IF_MATCH = "1"


def test_patch_replaces_marker_with_current_if_match(
    skills_client: SkillsClient, seed_skill: SeedSkill
) -> None:
    name = seed_skill()["name"]
    # The create response carries null timestamps, so the If-Match token comes from a read.
    current = skills_client.fetch(name)
    assert current.status_code == 200, f"reading the seeded skill failed: {current.text[:500]}"

    resp = skills_client.patch(
        f"/{name}/body",
        json={"old_string": SKILL_BODY_MARKER, "new_string": REPLACEMENT},
        headers={"If-Match": current.json()["updatedAt"]},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"status": "success"}

    stored = skills_client.fetch(name)
    assert stored.status_code == 200, stored.text[:500]
    body = stored.json()["body"]
    assert REPLACEMENT in body
    assert SKILL_BODY_MARKER not in body


@pytest.mark.parametrize(
    ("payload", "headers", "expected_status"),
    [
        pytest.param(
            {"old_string": "spec-audit-absent-text", "new_string": REPLACEMENT},
            {},
            400,
            id="old_string_not_in_body",
        ),
        pytest.param(
            {"old_string": SKILL_BODY_MARKER, "new_string": REPLACEMENT},
            {"If-Match": STALE_IF_MATCH},
            409,
            id="stale_if_match",
        ),
        pytest.param(
            {"old_string": SKILL_BODY_MARKER},
            {},
            422,
            id="new_string_missing",
        ),
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
    assert_strict_openapi_response(resp, ROUTE)
    assert "detail" in resp.json()

    stored = skills_client.fetch(name)
    assert stored.status_code == 200, stored.text[:500]
    assert SKILL_BODY_MARKER in stored.json()["body"]


def test_patch_missing_skill_is_not_found(skills_client: SkillsClient) -> None:
    # The built-in guard loads the skill first, so a missing name never reaches the 400 branch.
    resp = skills_client.patch(
        f"/{MISSING_SKILL_NAME}/body",
        json={"old_string": SKILL_BODY_MARKER, "new_string": REPLACEMENT},
    )
    if resp.status_code == 403:
        assert_strict_openapi_response(resp, ROUTE)
        pytest.skip(f"skills are not usable on this stack: {resp.text[:200]}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
