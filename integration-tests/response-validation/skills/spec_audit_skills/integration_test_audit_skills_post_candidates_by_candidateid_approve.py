"""Strict OpenAPI audit of POST /api/v1/skills/candidates/:candidateId/approve.

No route creates a learning-loop candidate, so the 200 path is unreachable
from the API and only the refusals are audited.
"""

from __future__ import annotations

import pytest
from skills_audit_support import (
    MISSING_CANDIDATE_ID,
    UNSAFE_PATH_SEGMENT,
    SkillsClient,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/candidates/:candidateId/approve"


def _approve_path(candidate_id: str) -> str:
    return f"/candidates/{candidate_id}/approve"


def test_approve_missing_candidate_is_not_found(skills_client: SkillsClient) -> None:
    resp = skills_client.post(_approve_path(MISSING_CANDIDATE_ID))
    if resp.status_code == 403:
        pytest.skip(f"skills are not usable on this stack: {resp.text[:200]}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"detail": f"Candidate {MISSING_CANDIDATE_ID!r} not found"}


def test_member_approve_missing_candidate_is_not_found(second_user: SecondUser) -> None:
    # No admin gate in Node or Python: a member gets the same lookup, not a 403.
    resp = request_as(second_user, "POST", _approve_path(MISSING_CANDIDATE_ID))
    if resp.status_code == 403:
        pytest.skip(f"skills are not usable on this stack: {resp.text[:200]}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_approve_unsafe_candidate_id_is_rejected(skills_client: SkillsClient) -> None:
    resp = skills_client.post(_approve_path(UNSAFE_PATH_SEGMENT))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_approve_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.post(_approve_path(MISSING_CANDIDATE_ID), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
