"""Strict OpenAPI audit of GET /api/v1/configurationManager/prompts/system."""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/prompts/system"

PROMPT_KEYS = {
    "customSystemPrompt",
    "customSystemPromptWebSearch",
    "customSystemPromptAgent",
}


def test_admin_gets_all_three_prompts(config_client: ConfigClient) -> None:
    resp = config_client.get("/prompts/system")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    # Every branch of the controller (dedicated key, legacy aiModels blob, nothing
    # stored) answers the same three keys, with "" standing in for an unset prompt.
    assert set(body) == PROMPT_KEYS
    assert all(isinstance(value, str) for value in body.values())


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no_token", "invalid_token"],
)
def test_unauthenticated_is_rejected(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.get("/prompts/system", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", "/prompts/system")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
