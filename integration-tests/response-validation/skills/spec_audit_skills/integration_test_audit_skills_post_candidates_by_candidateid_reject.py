"""Strict OpenAPI audit of POST /api/v1/skills/candidates/:candidateId/reject."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from helper.second_user import SecondUser
from skills_audit_support import (
    MISSING_CANDIDATE_ID,
    UNSAFE_PATH_SEGMENT,
    SkillsClient,
    request_as,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/candidates/:candidateId/reject"
REJECT_MISSING = f"/candidates/{MISSING_CANDIDATE_ID}/reject"


def _assert_rejected(resp: requests.Response) -> None:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"status": "success"}


@pytest.mark.asyncio(loop_scope="session")
async def test_reject_removes_candidate_without_creating_skill(
    skills_client: SkillsClient, second_user: SecondUser, seed_skill_candidate: Any
) -> None:
    seeded = await seed_skill_candidate()

    # The review queue is org-wide: a member can discard a candidate it did not produce.
    _assert_rejected(request_as(second_user, "POST", f"/candidates/{seeded['candidateId']}/reject"))

    pending = skills_client.get("/candidates/pending").json()["candidates"]
    assert seeded["candidateId"] not in {c["candidate_id"] for c in pending}
    assert skills_client.fetch(seeded["name"]).status_code == 404


def test_reject_unknown_candidate_succeeds(skills_client: SkillsClient) -> None:
    # Rejecting is a blind delete: an id that matches nothing is still a success.
    _assert_rejected(skills_client.post(REJECT_MISSING))


def test_reject_unsafe_candidate_id_is_rejected(skills_client: SkillsClient) -> None:
    resp = skills_client.post(f"/candidates/{UNSAFE_PATH_SEGMENT}/reject")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_reject_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.post(REJECT_MISSING, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
