"""Strict OpenAPI audit of POST /api/v1/skills/import/npm/preview."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from skills_audit_support import SkillsClient, request_as
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/import/npm/preview"
PATH = "/import/npm/preview"

# Refused by the Python command parser before any registry lookup, so no internet is needed.
SHELL_INJECTION_COMMAND = "npm install left-pad; rm -rf /"
UNKNOWN_FLAG_COMMAND = "npm install --registry"


def _skip_if_skills_disabled(resp: requests.Response) -> None:
    # The feature-flag dependency answers 403 ahead of body validation.
    if resp.status_code == 403:
        pytest.skip(f"skills are not usable on this stack: {resp.text[:200]}")


def test_npm_preview_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.post(PATH, auth=False, json={"command_or_name": "left-pad"})

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="missing-command"),
        pytest.param({"command_or_name": ""}, id="empty-command"),
    ],
)
def test_npm_preview_rejects_invalid_body(skills_client: SkillsClient, body: dict[str, Any]) -> None:
    resp = skills_client.post(PATH, json=body)
    _skip_if_skills_disabled(resp)

    assert resp.status_code == 422, resp.text[:500]
    assert isinstance(resp.json()["detail"], list)
    assert_strict_openapi_response(resp, ROUTE)


def test_npm_preview_rejects_unsafe_command(skills_client: SkillsClient) -> None:
    resp = skills_client.post(PATH, json={"command_or_name": SHELL_INJECTION_COMMAND})
    _skip_if_skills_disabled(resp)

    assert resp.status_code == 400, resp.text[:500]
    assert isinstance(resp.json()["detail"], str)
    assert_strict_openapi_response(resp, ROUTE)


def test_npm_preview_as_member_reaches_the_parser(second_user: Any) -> None:
    # No admin gate: a member gets the parser's 400, not a 403.
    resp = request_as(second_user, "POST", PATH, json={"command_or_name": UNKNOWN_FLAG_COMMAND})
    _skip_if_skills_disabled(resp)

    assert resp.status_code == 400, resp.text[:500]
    assert isinstance(resp.json()["detail"], str)
    assert_strict_openapi_response(resp, ROUTE)
