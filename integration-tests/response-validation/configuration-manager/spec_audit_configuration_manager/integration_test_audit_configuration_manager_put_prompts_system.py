"""Strict OpenAPI audit of PUT /api/v1/configurationManager/prompts/system.

Negative paths only: a successful call replaces the org-wide system prompts, and
when they still live in the legacy aiModels blob the dedicated key it creates
cannot be removed again.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/prompts/system"
PATH = "/prompts/system"

# The controller refuses this before it touches the store, so a gate that
# wrongly let a caller through still could not overwrite the prompts.
UNWRITABLE_BODY: dict[str, Any] = {}


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no_token", "invalid_token"],
)
def test_set_system_prompt_requires_valid_token(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.put(PATH, auth=False, headers=headers, json=UNWRITABLE_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_set_system_prompt_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "PUT", PATH, json=UNWRITABLE_BODY)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        # customSystemPromptWebSearch is required although the spec schema marks nothing required.
        {"customSystemPrompt": "spec-audit"},
        {
            "customSystemPrompt": "spec-audit",
            "customSystemPromptWebSearch": "spec-audit",
            "customSystemPromptAgent": 1,
        },
    ],
    ids=["missing_web_search_prompt", "agent_prompt_not_a_string"],
)
def test_set_system_prompt_invalid_body_is_rejected(
    config_client: ConfigClient, body: dict[str, Any]
) -> None:
    resp = config_client.put(PATH, json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
