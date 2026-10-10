"""Strict OpenAPI audit of GET /api/v1/skills/candidates/pending.

No route creates a candidate (the learning loop queues them after an agent run), so the
tests write one into the graph the way the store does.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests
from helper.second_user import SecondUser
from skills_audit_support import SkillsClient, request_as
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/candidates/pending"
PATH = "/candidates/pending"


def _candidates(resp: requests.Response) -> list[dict[str, Any]]:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert set(body) == {"candidates"}
    return list(body["candidates"])


@pytest.mark.asyncio(loop_scope="session")
async def test_admin_lists_seeded_candidate(skills_client: SkillsClient, seed_skill_candidate: Any) -> None:
    seeded = await seed_skill_candidate()

    listed = {c["candidate_id"]: c for c in _candidates(skills_client.get(PATH))}
    candidate = listed[seeded["candidateId"]]
    # Python serialises SkillCandidate with its snake_case field names.
    assert candidate["name"] == seeded["name"]
    assert candidate["status"] == "pending"
    assert candidate["source_session_id"] is None
    assert candidate["confidence"] == 1.0
    assert candidate["created_at"] == str(seeded["createdAtTimestamp"])


@pytest.mark.asyncio(loop_scope="session")
async def test_member_sees_the_org_wide_queue(second_user: SecondUser, seed_skill_candidate: Any) -> None:
    # No admin gate and no creator scope: the review queue is org-wide.
    seeded = await seed_skill_candidate()
    ids = {c["candidate_id"] for c in _candidates(request_as(second_user, "GET", PATH))}
    assert seeded["candidateId"] in ids


def test_unknown_query_parameter_is_ignored(skills_client: SkillsClient) -> None:
    with outside_request_contract("an undocumented query parameter, to show it is ignored"):
        resp = skills_client.get(PATH, params={"status": "spec-audit-ignored"})
        _candidates(resp)


def test_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.get(PATH, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_malformed_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.get(
        PATH, auth=False, headers={"Authorization": "Bearer spec-audit-not-a-jwt"}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
