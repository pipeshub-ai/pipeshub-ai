"""Strict OpenAPI audit of GET /api/v1/configurationManager/ai-models/registry/:providerId/schema."""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import (
    MALFORMED_AI_PROVIDER_ID,
    UNKNOWN_AI_PROVIDER_ID,
)
from helper.clients.config_client import ConfigClient
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/ai-models/registry/:providerId/schema"

# Registered at import time by app/config/ai_models/providers/openai.py.
KNOWN_PROVIDER_ID = "openAI"
KNOWN_PROVIDER_CAPABILITIES = {
    "text_generation",
    "embedding",
    "image_generation",
    "tts",
    "stt",
}


def _path(provider_id: str) -> str:
    return f"/ai-models/registry/{provider_id}/schema"


def test_admin_gets_fields_for_every_capability(config_client: ConfigClient) -> None:
    resp = config_client.get(_path(KNOWN_PROVIDER_ID))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    assert body["provider"] == {"providerId": KNOWN_PROVIDER_ID, "name": "OpenAI"}
    # The Python registry nests the capability map under "fields".
    fields = body["schema"]["fields"]
    assert set(fields) == KNOWN_PROVIDER_CAPABILITIES
    for capability, descriptors in fields.items():
        assert descriptors, f"no fields for {capability}"
        for descriptor in descriptors:
            assert {"name", "displayName", "fieldType", "required"} <= set(descriptor)


def test_capability_query_narrows_the_schema(config_client: ConfigClient) -> None:
    resp = config_client.get(_path(KNOWN_PROVIDER_ID), params={"capability": "embedding"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    fields = resp.json()["schema"]["fields"]
    assert set(fields) == {"embedding"}
    assert fields["embedding"]


def test_unknown_provider_is_not_found(config_client: ConfigClient) -> None:
    resp = config_client.get(_path(UNKNOWN_AI_PROVIDER_ID))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    # Node relays FastAPI's HTTPException body as is, not its own error envelope.
    assert resp.json() == {"detail": f"Provider '{UNKNOWN_AI_PROVIDER_ID}' not found"}


def test_unsafe_provider_id_is_rejected_before_the_proxy(config_client: ConfigClient) -> None:
    resp = config_client.get(_path(MALFORMED_AI_PROVIDER_ID))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_no_token_is_rejected(config_client: ConfigClient) -> None:
    resp = config_client.get(_path(KNOWN_PROVIDER_ID), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
