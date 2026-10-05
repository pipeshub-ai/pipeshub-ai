"""Strict OpenAPI audit of GET /api/v1/configurationManager/ai-models/registry/capabilities."""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/ai-models/registry/capabilities"
PATH = "/ai-models/registry/capabilities"

# ModelCapability value -> CAPABILITY_TO_MODEL_TYPE bucket (backend/python/app/config/ai_models/types.py).
CAPABILITY_MODEL_TYPES = {
    "text_generation": "llm",
    "embedding": "embedding",
    "image_generation": "imageGeneration",
    "tts": "tts",
    "stt": "stt",
    "video": "video",
    "ocr": "ocr",
    "reasoning": "reasoning",
}


def test_admin_lists_registry_capabilities(config_client: ConfigClient) -> None:
    # Node forwards the Python query service's reply unchanged (status and body).
    resp = config_client.get(PATH)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert set(body) == {"success", "capabilities"}
    assert body["success"] is True
    for capability in body["capabilities"]:
        assert set(capability) == {"id", "name", "modelType"}
        assert capability["name"] == capability["id"].replace("_", " ").title()
    assert {c["id"]: c["modelType"] for c in body["capabilities"]} == CAPABILITY_MODEL_TYPES


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no_token", "invalid_token"],
)
def test_unauthenticated_is_rejected(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.get(PATH, auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    # userAdminCheck refuses before the proxy call, so the Python service is never reached.
    resp = request_as(second_user, "GET", PATH)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
