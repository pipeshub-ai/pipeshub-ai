"""Strict OpenAPI audit of PUT /api/v1/configurationManager/prompts/system.

Chain: authenticate -> requireScopes(config:write) -> userAdminCheck -> setCustomSystemPrompt.
No validator: the handler checks the types itself and answers a hand-written 400. The prompts
are org-wide and other suites chat while this runs, so the success cases only re-save the
prompts in force, and the stored bytes are put back afterwards.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    KV_SYSTEM_PROMPTS,
    forget_stored_config,
    read_stored_value,
    request_as,
    write_stored_value,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/prompts/system"
PATH = "/prompts/system"
SAVED_MESSAGE = "Custom system prompts updated successfully"


@pytest.fixture
def prompts_in_force(config_client: ConfigClient) -> Iterator[dict[str, str]]:
    """The prompts GET answers now; the stored key is put back byte for byte afterwards."""
    raw = read_stored_value(KV_SYSTEM_PROMPTS)
    resp = config_client.get(PATH)
    assert resp.status_code == 200, resp.text[:500]
    prompts: dict[str, str] = resp.json()
    try:
        yield dict(prompts)
    finally:
        if raw is None:
            forget_stored_config(KV_SYSTEM_PROMPTS)
        else:
            write_stored_value(KV_SYSTEM_PROMPTS, raw)
        assert config_client.get(PATH).json() == prompts


def test_saving_the_prompts_in_force_echoes_them(
    config_client: ConfigClient, prompts_in_force: dict[str, str]
) -> None:
    resp = config_client.put(PATH, json=prompts_in_force)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"message": SAVED_MESSAGE, **prompts_in_force}
    assert config_client.get(PATH).json() == prompts_in_force


def test_the_agent_prompt_may_be_left_out_and_is_then_saved_empty(
    config_client: ConfigClient, prompts_in_force: dict[str, str]
) -> None:
    if prompts_in_force["customSystemPromptAgent"]:
        pytest.fail("an agent prompt is set; leaving it out would erase it for the other suites")
    body = {key: prompts_in_force[key] for key in ("customSystemPrompt", "customSystemPromptWebSearch")}

    resp = config_client.put(PATH, json=body)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["customSystemPromptAgent"] == ""


def test_fields_the_handler_does_not_read_are_ignored(
    config_client: ConfigClient, prompts_in_force: dict[str, str]
) -> None:
    with outside_request_contract("specAudit is not a field of the body"):
        resp = config_client.put(PATH, json={**prompts_in_force, "specAudit": "x"})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert "specAudit" not in resp.json()
    assert config_client.get(PATH).json() == prompts_in_force


@pytest.mark.parametrize(
    ("body", "message"),
    [
        pytest.param({}, "customSystemPrompt must be a string", id="empty-object"),
        pytest.param(
            {"customSystemPromptWebSearch": "spec-audit"}, "customSystemPrompt must be a string", id="missing-prompt"
        ),
        pytest.param(
            {"customSystemPrompt": "spec-audit"},
            "customSystemPromptWebSearch must be a string",
            id="missing-web-search-prompt",
        ),
        pytest.param(
            {"customSystemPrompt": 1, "customSystemPromptWebSearch": "spec-audit"},
            "customSystemPrompt must be a string",
            id="prompt-not-a-string",
        ),
        pytest.param(
            {"customSystemPrompt": "spec-audit", "customSystemPromptWebSearch": "spec-audit", "customSystemPromptAgent": 1},
            "customSystemPromptAgent must be a string",
            id="agent-prompt-not-a-string",
        ),
        pytest.param(
            {"customSystemPrompt": "spec-audit", "customSystemPromptWebSearch": "spec-audit", "customSystemPromptAgent": None},
            "customSystemPromptAgent must be a string",
            id="agent-prompt-null",
        ),
    ],
)
def test_invalid_body_is_refused_by_the_handler_and_nothing_is_stored(
    config_client: ConfigClient, body: dict[str, Any], message: str
) -> None:
    before = config_client.get(PATH).json()

    resp = config_client.put(PATH, json=body)

    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", message)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)
    assert config_client.get(PATH).json() == before


@pytest.mark.parametrize("headers", [None, INVALID_BEARER_HEADERS], ids=["no-token", "invalid-token"])
def test_set_system_prompt_requires_valid_token(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.put(PATH, auth=False, headers=headers, json={})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_set_system_prompt_member_is_forbidden(config_client: ConfigClient, second_user: SecondUser) -> None:
    before = config_client.get(PATH).json()

    resp = request_as(
        second_user, "PUT", PATH, json={"customSystemPrompt": "spec-audit", "customSystemPromptWebSearch": "spec-audit"}
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert config_client.get(PATH).json() == before
