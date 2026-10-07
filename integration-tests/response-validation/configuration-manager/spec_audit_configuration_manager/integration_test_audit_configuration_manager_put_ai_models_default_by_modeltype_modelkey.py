"""Strict OpenAPI audit of PUT /api/v1/configurationManager/ai-models/default/:modelType/:modelKey.

The org's default llm is handed to an entry this test added (the same Azure model the stack
uses) and given back before the entry is deleted. Embedding defaults are never touched: that
change rebuilds the vector store.
"""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    MISSING_AI_MODEL_KEY,
    SeedLlmProvider,
    assert_validation_error,
    azure_llm_configuration,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/ai-models/default/:modelType/:modelKey"
HEALTH_CHECK_TIMEOUT = 120


def _default_llm_key(config_client: ConfigClient) -> str | None:
    resp = config_client.get("/ai-models/llm")
    assert resp.status_code == 200, resp.text[:500]
    return next((m["modelKey"] for m in resp.json()["models"] if m.get("isDefault")), None)


def test_an_entry_becomes_the_default_after_its_health_check(
    config_client: ConfigClient, seed_llm_provider: SeedLlmProvider
) -> None:
    previous = _default_llm_key(config_client)
    if previous is None:
        pytest.fail("environment: no default llm is configured, so the default could not be handed back")
    key = seed_llm_provider()["modelKey"]

    try:
        resp = config_client.put(f"/ai-models/default/llm/{key}", timeout=HEALTH_CHECK_TIMEOUT)

        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert resp.json() == {
            "status": "success",
            "message": "Default llm model updated successfully",
            "details": {
                "modelKey": key,
                "modelType": "llm",
                "provider": "azureOpenAI",
                "model": azure_llm_configuration()["model"],
            },
        }
        assert _default_llm_key(config_client) == key

        again = config_client.put(f"/ai-models/default/llm/{key}", timeout=HEALTH_CHECK_TIMEOUT)
        assert again.status_code == 200, again.text[:500]
        assert_strict_openapi_exchange(again, ROUTE)
        # Already the default: no health check and nothing is written.
        assert again.json()["message"] == "Default llm model unchanged"
    finally:
        restored = config_client.put(f"/ai-models/default/llm/{previous}", timeout=HEALTH_CHECK_TIMEOUT)
        assert restored.status_code == 200, f"default llm not handed back: {restored.text[:300]}"


def test_a_key_of_another_type_is_a_400(config_client: ConfigClient, seed_llm_provider: SeedLlmProvider) -> None:
    key = seed_llm_provider()["modelKey"]

    resp = config_client.put(f"/ai-models/default/embedding/{key}")

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"status": "error", "message": f"Model key '{key}' belongs to type 'llm', not 'embedding'"}


def test_an_unknown_key_is_an_internal_error(config_client: ConfigClient) -> None:
    # API bug: the lookup walks every top-level key of the stored config, modelRoles (an
    # object, not a list) included, and throws before it can answer 404.
    resp = config_client.put(f"/ai-models/default/llm/{MISSING_AI_MODEL_KEY}")

    assert resp.status_code == 500, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR"


def test_unknown_model_type_is_rejected(config_client: ConfigClient) -> None:
    resp = config_client.put(f"/ai-models/default/spec-audit-type/{MISSING_AI_MODEL_KEY}")

    assert_validation_error(resp, "params.modelType")
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("headers", [None, INVALID_BEARER_HEADERS], ids=["no-token", "invalid-token"])
def test_unauthenticated_is_rejected(config_client: ConfigClient, headers: dict[str, str] | None) -> None:
    resp = config_client.put(f"/ai-models/default/llm/{MISSING_AI_MODEL_KEY}", auth=False, headers=headers)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "PUT", f"/ai-models/default/llm/{MISSING_AI_MODEL_KEY}")

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_config_write_scope_is_forbidden(
    config_client: ConfigClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = config_client.put(
        f"/ai-models/default/llm/{MISSING_AI_MODEL_KEY}", auth=False, headers=narrow_scope_headers
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
