"""Strict OpenAPI audit of POST /api/v1/skills/candidates/:candidateId/approve.

No route creates a learning-loop candidate, so the tests write one into the graph the way
the store does (``seed_skill_candidate``).
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.second_user import SecondUser
from skills_audit_support import (
    MISSING_CANDIDATE_ID,
    SKILL_BODY_MARKER,
    UNSAFE_PATH_SEGMENT,
    SeedSkill,
    SkillsClient,
    request_as,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/candidates/:candidateId/approve"


def _approve_path(candidate_id: str) -> str:
    return f"/candidates/{candidate_id}/approve"


@pytest.mark.asyncio(loop_scope="session")
async def test_approve_promotes_candidate_to_active_skill(
    skills_client: SkillsClient, seed_skill_candidate: Any
) -> None:
    seeded = await seed_skill_candidate()
    name = seeded["name"]
    try:
        resp = skills_client.post(_approve_path(seeded["candidateId"]))
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        created = resp.json()
        assert created["name"] == name
        assert created["status"] == "active"
        assert created["category"] == "spec-audit"

        skill = skills_client.fetch(name)
        assert skill.status_code == 200, skill.text[:500]
        assert SKILL_BODY_MARKER in skill.json()["body"]
        pending = skills_client.get("/candidates/pending").json()["candidates"]
        assert seeded["candidateId"] not in {c["candidate_id"] for c in pending}
    finally:
        skills_client.remove(name, detach="true")


@pytest.mark.asyncio(loop_scope="session")
async def test_approve_candidate_named_like_existing_skill_is_conflict(
    skills_client: SkillsClient, seed_skill: SeedSkill, seed_skill_candidate: Any
) -> None:
    name = seed_skill()["name"]
    seeded = await seed_skill_candidate(name=name)

    resp = skills_client.post(_approve_path(seeded["candidateId"]))
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"detail": f"Skill {name!r} already exists"}


def test_approve_missing_candidate_is_not_found(skills_client: SkillsClient) -> None:
    resp = skills_client.post(_approve_path(MISSING_CANDIDATE_ID))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"detail": f"Candidate {MISSING_CANDIDATE_ID!r} not found"}


def test_member_approve_missing_candidate_is_not_found(second_user: SecondUser) -> None:
    # No admin gate in Node or Python: a member gets the same lookup, not a 403.
    resp = request_as(second_user, "POST", _approve_path(MISSING_CANDIDATE_ID))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_approve_unsafe_candidate_id_is_rejected(skills_client: SkillsClient) -> None:
    resp = skills_client.post(_approve_path(UNSAFE_PATH_SEGMENT))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_approve_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.post(_approve_path(MISSING_CANDIDATE_ID), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
