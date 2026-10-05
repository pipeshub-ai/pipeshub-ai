"""Strict OpenAPI audit of GET /api/v1/skills/candidates/pending."""

from __future__ import annotations

import pytest
import requests
from skills_audit_support import SkillsClient, request_as
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/candidates/pending"
PATH = "/candidates/pending"


def _assert_candidate_list(resp: requests.Response) -> None:
    if resp.status_code == 403:
        assert_strict_openapi_response(resp, ROUTE)
        pytest.skip(f"skills are not usable on this stack: {resp.text[:200]}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert set(body) == {"candidates"}
    assert isinstance(body["candidates"], list)
    for candidate in body["candidates"]:
        # Python serialises SkillCandidate with its snake_case field names.
        assert {"candidate_id", "name", "description", "body"} <= set(candidate)


def test_admin_lists_pending_candidates(skills_client: SkillsClient) -> None:
    # Unknown query params are forwarded to Python, which ignores them.
    resp = skills_client.get(PATH, params={"status": "spec-audit-ignored"})
    _assert_candidate_list(resp)


def test_member_lists_pending_candidates(second_user: SecondUser) -> None:
    # No admin gate: the review queue is org-scoped and readable by any member.
    resp = request_as(second_user, "GET", PATH)
    _assert_candidate_list(resp)


def test_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.get(PATH, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_malformed_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.get(
        PATH, auth=False, headers={"Authorization": "Bearer spec-audit-not-a-jwt"}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
