"""Strict OpenAPI audit of GET /api/v1/configurationManager/aiModelsConfig."""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/aiModelsConfig"

# AI_PUBLIC_CONFIG_KEYS in utils/maskConfigSecrets.ts: the only keys an entry's configuration may carry.
PUBLIC_CONFIGURATION_KEYS = {"model", "modelFriendlyName", "dimensions", "defaultReasoningEffort"}


def test_admin_gets_stored_config_without_provider_secrets(config_client: ConfigClient) -> None:
    resp = config_client.get("/aiModelsConfig")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    # {} when nothing is stored; otherwise the raw KV shape, with no envelope around it.
    assert isinstance(body, dict), body
    leaked = {
        f"{bucket}[{index}].configuration.{key}"
        for bucket, entries in body.items()
        if isinstance(entries, list)
        for index, entry in enumerate(entries)
        if isinstance(entry, dict) and isinstance(entry.get("configuration"), dict)
        for key in entry["configuration"]
        if key not in PUBLIC_CONFIGURATION_KEYS
    }
    assert not leaked, f"configuration keys outside the public allowlist: {sorted(leaked)}"


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no_token", "invalid_token"],
)
def test_unauthenticated_is_rejected(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.get("/aiModelsConfig", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", "/aiModelsConfig")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_config_read_scope_is_forbidden(config_client: ConfigClient, narrow_scope_headers: dict[str, str]) -> None:
    resp = config_client.get("/aiModelsConfig", auth=False, headers=narrow_scope_headers)

    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
