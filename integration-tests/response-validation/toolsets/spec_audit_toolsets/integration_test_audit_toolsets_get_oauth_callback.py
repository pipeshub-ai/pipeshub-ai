"""Strict OpenAPI audit of GET /api/v1/toolsets/oauth/callback.

Python answers every refusal with a 200 JSON body, which Node reshapes to camelCase.
No case here completes a flow: the token exchange goes to the provider's own token
endpoint, which the toolset registry fixes and no request can redirect.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest
import requests
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)
from toolsets_audit_support import (
    MISSING_AGENT_KEY,
    MISSING_INSTANCE_ID,
    OAUTH_ONLY_LOCAL_TOOLSET_TYPE,
    JsonObject,
    SeedToolsetInstance,
    ToolsetsClient,
    error_of,
    mark_code_as_exchanged,
    oauth_state,
    rejected_fields,
    request_as,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/oauth/callback"
BASE_URL = "https://spec-audit.invalid"
DEFAULT_BASE_URL = "http://localhost:3001"
CODE = "spec-audit-code"
AUTH_FAILED_MESSAGE = "Authentication failed. You may not have permission to complete this action."


def _assert_refusal(
    resp: requests.Response, error: str, *, base_url: str = BASE_URL, message: str | None = None
) -> None:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    expected: JsonObject = {
        "success": False,
        "error": error,
        "redirectUrl": f"{base_url}/tools?oauth_error={error}",
    }
    if message is not None:
        expected["errorMessage"] = message
    assert resp.json() == expected


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"code": CODE},
        {"state": "spec-audit-state"},
        {"code": "", "state": ""},
        # These four values of `error` count as "no error".
        {"error": "null"},
        {"error": "undefined"},
        {"error": "None"},
        {"error": ""},
        {"base_url": ""},
    ],
    ids=[
        "no-query",
        "code-only",
        "state-only",
        "empty-code-and-state",
        "error-null",
        "error-undefined",
        "error-None",
        "error-empty",
        "base-url-empty",
    ],
)
def test_callback_without_code_and_state_reports_missing_parameters(
    toolsets_client: ToolsetsClient, params: dict[str, str]
) -> None:
    resp = toolsets_client.get("/oauth/callback", params=params)
    # Without base_url the redirect goes to Python's built-in default.
    _assert_refusal(resp, "missing_parameters", base_url=DEFAULT_BASE_URL)


@pytest.mark.parametrize(
    "error",
    ["access_denied", "a b&c=d"],
    ids=["provider-error", "error-is-not-url-encoded"],
)
def test_callback_relays_the_providers_error(toolsets_client: ToolsetsClient, error: str) -> None:
    resp = toolsets_client.get(
        "/oauth/callback",
        params={"base_url": BASE_URL, "error": error, "code": CODE, "state": "spec-audit-state"},
    )
    _assert_refusal(resp, error)


@pytest.mark.parametrize(
    ("state", "message"),
    [
        ("not-a-state", "OAuth configuration error: Failed to decode OAuth state: Incorrect padding"),
        (
            base64.urlsafe_b64encode(b"spec audit").decode(),
            "OAuth configuration error: Invalid OAuth state format: not valid JSON",
        ),
        (
            base64.urlsafe_b64encode(b'{"state": "s"}').decode(),
            "OAuth configuration error: Failed to decode OAuth state: Missing required fields in state data",
        ),
    ],
    ids=["not-base64", "not-json", "fields-missing"],
)
def test_callback_with_undecodable_state_reports_oauth_config_error(
    toolsets_client: ToolsetsClient, state: str, message: str
) -> None:
    resp = toolsets_client.get(
        "/oauth/callback", params={"base_url": BASE_URL, "code": CODE, "state": state}
    )
    _assert_refusal(resp, "OAuthConfigError", message=message)


def test_callback_for_an_instance_without_oauth_reports_oauth_config_error(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance()

    resp = toolsets_client.get(
        "/oauth/callback",
        params={"base_url": BASE_URL, "code": CODE, "state": oauth_state(seeded["_id"], seeded["createdBy"])},
    )
    _assert_refusal(
        resp,
        "OAuthConfigError",
        message="OAuth configuration error: Instance has no OAuth configuration.",
    )


def test_callback_for_another_users_flow_reports_auth_failed(
    toolsets_client: ToolsetsClient,
    second_user: SecondUser,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    seeded = seed_toolset_instance(oauth=True)
    params = {
        "base_url": BASE_URL,
        "code": CODE,
        "state": oauth_state(seeded["_id"], seeded["createdBy"]),
    }

    # The state names the admin; the member presenting it is refused before any lookup.
    resp = request_as(second_user, "GET", "/oauth/callback", params=params)
    _assert_refusal(resp, "auth_failed", message=AUTH_FAILED_MESSAGE)


def test_callback_for_an_unknown_instance_reports_auth_failed(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    admin_id = seed_toolset_instance()["createdBy"]

    resp = toolsets_client.get(
        "/oauth/callback",
        params={"base_url": BASE_URL, "code": CODE, "state": oauth_state(MISSING_INSTANCE_ID, admin_id)},
    )
    _assert_refusal(resp, "auth_failed", message=AUTH_FAILED_MESSAGE)


def test_callback_with_a_state_that_was_never_issued_reports_server_error(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance(oauth=True)

    # Well-formed, right user, real OAuth instance: only the inner state is unknown, so
    # the flow stops at the stored-state comparison, before any call to the provider.
    resp = toolsets_client.get(
        "/oauth/callback",
        params={"base_url": BASE_URL, "code": CODE, "state": oauth_state(seeded["_id"], seeded["createdBy"])},
    )
    _assert_refusal(
        resp,
        "server_error",
        message="The authentication session has expired. Please close this window and try again.",
    )

    untouched = toolsets_client.get(f"/instances/{seeded['_id']}/status")
    assert untouched.status_code == 200, untouched.text[:500]
    assert untouched.json()["isAuthenticated"] is False


def test_callback_repeated_with_an_already_exchanged_code_completes_the_flow(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    # The one success that needs no provider: a callback that arrives again with a code
    # the stored record lists as exchanged is answered from the stored token.
    seeded = seed_toolset_instance(oauth=True, toolsetType=OAUTH_ONLY_LOCAL_TOOLSET_TYPE)
    instance_id = seeded["_id"]
    started = toolsets_client.get(f"/instances/{instance_id}/oauth/authorize", params={"base_url": BASE_URL})
    assert started.status_code == 200, started.text[:500]
    mark_code_as_exchanged(instance_id, seeded["createdBy"], CODE)

    resp = toolsets_client.get(
        "/oauth/callback",
        params={"base_url": BASE_URL, "code": CODE, "state": started.json()["state"]},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "success": True,
        "redirectUrl": f"{BASE_URL}/tools?oauth_success=true&instance_id={instance_id}",
    }

    status = toolsets_client.get(f"/instances/{instance_id}/status")
    assert status.status_code == 200, status.text[:500]
    assert status.json()["isAuthenticated"] is True


def test_callback_of_an_agent_flow_for_an_unknown_agent_reports_permission_denied(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance(oauth=True)

    resp = toolsets_client.get(
        "/oauth/callback",
        params={
            "base_url": BASE_URL,
            "code": CODE,
            "state": oauth_state(seeded["_id"], MISSING_AGENT_KEY, is_agent=True),
        },
    )
    _assert_refusal(
        resp,
        "agent_permission_denied",
        message=(
            "You don't have permission to connect tools for this agent. "
            "Ask the agent's owner to give you edit access, then try again."
        ),
    )


@pytest.mark.parametrize("base_url", ["ftp://spec-audit.invalid", "/relative", "spec-audit.invalid"])
def test_callback_refuses_a_base_url_that_is_not_http(toolsets_client: ToolsetsClient, base_url: str) -> None:
    # Python builds the redirect from base_url as is; Node refuses to relay one that
    # is not an absolute http(s) URL.
    resp = toolsets_client.get("/oauth/callback", params={"base_url": base_url})
    assert resp.status_code == 400, resp.text[:500]
    error = error_of(resp)
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", "Invalid redirect URL from backend")
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize("name", ["code", "state", "error", "base_url"])
def test_callback_rejects_a_repeated_query_parameter(toolsets_client: ToolsetsClient, name: str) -> None:
    # Express parses a repeated key into an array, which the zod string refuses.
    resp = toolsets_client.get("/oauth/callback", params={name: ["a", "b"]})
    assert resp.status_code == 400, resp.text[:500]
    assert rejected_fields(resp) == {f"query.{name}"}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_callback_ignores_unknown_query_parameters(toolsets_client: ToolsetsClient) -> None:
    params: dict[str, Any] = {"base_url": BASE_URL, "scope": "read:jira-work", "session_state": "x"}
    with outside_request_contract("providers append their own parameters; the validator drops them"):
        resp = toolsets_client.get("/oauth/callback", params=params)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json()["error"] == "missing_parameters"


def test_callback_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get("/oauth/callback", params={"code": CODE, "state": "s"}, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
