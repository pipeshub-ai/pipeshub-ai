"""Strict OpenAPI audit of GET /api/v1/configurationManager/ai-models/registry/:providerId/schema.

Chain: authenticate -> requireScopes(config:read) -> userAdminCheck -> proxy to the query
service, whose status and body are relayed as they are. The router's path-parameter guard
refuses an id that decodes to something unsafe before the proxy runs.
"""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    MALFORMED_AI_PROVIDER_ID,
    UNKNOWN_AI_PROVIDER_ID,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/ai-models/registry/:providerId/schema"

# Registered at import time by app/config/ai_models/providers/openai.py.
KNOWN_PROVIDER_ID = "openAI"
KNOWN_PROVIDER_CAPABILITIES = {"text_generation", "embedding", "image_generation", "tts", "stt"}


def _path(provider_id: str) -> str:
    return f"/ai-models/registry/{provider_id}/schema"


def test_admin_gets_fields_for_every_capability(config_client: ConfigClient) -> None:
    resp = config_client.get(_path(KNOWN_PROVIDER_ID))

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert set(body) == {"success", "provider", "schema"}
    assert body["success"] is True
    assert body["provider"] == {"providerId": KNOWN_PROVIDER_ID, "name": "OpenAI"}
    # The Python registry nests the capability map under "fields".
    fields = body["schema"]["fields"]
    assert set(fields) == KNOWN_PROVIDER_CAPABILITIES
    for capability, descriptors in fields.items():
        assert descriptors, f"no fields for {capability}"
        for descriptor in descriptors:
            assert {"name", "displayName", "fieldType", "required"} <= set(descriptor)


@pytest.mark.parametrize(
    ("capability", "has_fields"),
    [
        pytest.param("embedding", True, id="capability-it-has"),
        pytest.param("ocr", False, id="capability-it-lacks"),
        # Not validated anywhere: an unknown name is just a capability no provider has.
        pytest.param("spec-audit-no-such-capability", False, id="unknown-capability"),
    ],
)
def test_capability_query_narrows_the_schema_to_that_key(
    config_client: ConfigClient, capability: str, has_fields: bool
) -> None:
    resp = config_client.get(_path(KNOWN_PROVIDER_ID), params={"capability": capability})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    fields = resp.json()["schema"]["fields"]
    assert set(fields) == {capability}
    assert bool(fields[capability]) is has_fields


def test_unknown_provider_is_not_found(config_client: ConfigClient) -> None:
    resp = config_client.get(_path(UNKNOWN_AI_PROVIDER_ID))

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    # Node relays FastAPI's HTTPException body as is, not its own error envelope.
    assert resp.json() == {"detail": f"Provider '{UNKNOWN_AI_PROVIDER_ID}' not found"}


def test_unsafe_provider_id_is_rejected_before_the_proxy(config_client: ConfigClient) -> None:
    resp = config_client.get(_path(MALFORMED_AI_PROVIDER_ID))

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("headers", [None, INVALID_BEARER_HEADERS], ids=["no-token", "invalid-token"])
def test_without_valid_token_is_unauthorized(config_client: ConfigClient, headers: dict[str, str] | None) -> None:
    resp = config_client.get(_path(KNOWN_PROVIDER_ID), auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", _path(KNOWN_PROVIDER_ID))
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
