"""Strict OpenAPI audit of POST /api/v1/skills/candidates/:candidateId/reject."""

from __future__ import annotations

import pytest
import requests
from skills_audit_support import (
    MISSING_CANDIDATE_ID,
    UNSAFE_PATH_SEGMENT,
    SkillsClient,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/candidates/:candidateId/reject"
REJECT_MISSING = f"/candidates/{MISSING_CANDIDATE_ID}/reject"


def _assert_rejected(resp: requests.Response) -> None:
    if resp.status_code == 403:
        pytest.skip(f"skills are not usable on this stack: {resp.text[:200]}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"status": "success"}


def test_reject_unknown_candidate_succeeds(skills_client: SkillsClient) -> None:
    # Rejecting is a blind delete: an id that matches nothing is still a success.
    _assert_rejected(skills_client.post(REJECT_MISSING))


def test_member_can_reject(second_user: SecondUser) -> None:
    _assert_rejected(request_as(second_user, "POST", REJECT_MISSING))


def test_reject_unsafe_candidate_id_is_rejected(skills_client: SkillsClient) -> None:
    resp = skills_client.post(f"/candidates/{UNSAFE_PATH_SEGMENT}/reject")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_reject_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.post(REJECT_MISSING, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
