"""Strict OpenAPI audit of PUT /api/v1/configurationManager/ai-models/providers/:modelType/:modelKey.

Every write goes to an llm entry this test added (the run's own Azure model, never the default).
"""

from __future__ import annotations

from typing import Any

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
from strict_openapi import assert_spec_forbids_request, assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/ai-models/providers/:modelType/:modelKey"
HEALTH_CHECK_TIMEOUT = 120
VALID_BODY: dict[str, Any] = {"provider": "azureOpenAI", "configuration": {"model": "gpt"}}


def _stored(config_client: ConfigClient, model_key: str) -> dict[str, Any]:
    resp = config_client.get("/ai-models/llm")
    assert resp.status_code == 200, resp.text[:500]
    return next(m for m in resp.json()["models"] if m["modelKey"] == model_key)


def _public_configuration() -> dict[str, str]:
    """What the client gets back from a read: no apiKey, endpoint or deployment."""
    return {"model": azure_llm_configuration()["model"]}


def test_an_update_without_the_credentials_keeps_the_stored_ones(
    config_client: ConfigClient, seed_llm_provider: SeedLlmProvider
) -> None:
    key = seed_llm_provider()["modelKey"]
    configuration = {**_public_configuration(), "modelFriendlyName": "Spec audit renamed"}

    resp = config_client.put(
        f"/ai-models/providers/llm/{key}",
        json={"provider": "azureOpenAI", "configuration": configuration, "isReasoning": True, "contextLength": 4096.5},
        timeout=HEALTH_CHECK_TIMEOUT,
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "status": "success",
        "message": "LLM provider updated successfully",
        "details": {
            "modelKey": key,
            "modelType": "llm",
            "provider": "azureOpenAI",
            "model": configuration["model"],
            "contextLength": 4096.5,
            "isMultimodal": False,
            "isReasoning": True,
        },
    }
    stored = _stored(config_client, key)
    assert stored["configuration"] == configuration
    assert stored["modelFriendlyName"] == "Spec audit renamed"


def test_a_null_context_length_clears_the_stored_one(
    config_client: ConfigClient, seed_llm_provider: SeedLlmProvider
) -> None:
    key = seed_llm_provider(contextLength=2048)["modelKey"]

    resp = config_client.put(
        f"/ai-models/providers/llm/{key}",
        json={"provider": "azureOpenAI", "configuration": _public_configuration(), "contextLength": None},
        timeout=HEALTH_CHECK_TIMEOUT,
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["details"]["contextLength"] is None
    assert _stored(config_client, key)["contextLength"] is None


@pytest.mark.parametrize("context_length", [0, 1023, 20_000_001])
def test_a_context_length_outside_the_usable_range_is_refused_by_the_health_check(
    config_client: ConfigClient, seed_llm_provider: SeedLlmProvider, context_length: int
) -> None:
    key = seed_llm_provider(contextLength=2048)["modelKey"]

    resp = config_client.put(
        f"/ai-models/providers/llm/{key}",
        json={"provider": "azureOpenAI", "configuration": _public_configuration(), "contextLength": context_length},
        timeout=HEALTH_CHECK_TIMEOUT,
    )

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)
    assert resp.json()["error"]["message"].startswith(f"Context length {context_length} is not a usable window")
    assert _stored(config_client, key)["contextLength"] == 2048


def test_a_rejected_api_key_is_a_400_and_nothing_changes(
    config_client: ConfigClient, seed_llm_provider: SeedLlmProvider
) -> None:
    key = seed_llm_provider()["modelKey"]
    before = _stored(config_client, key)

    resp = config_client.put(
        f"/ai-models/providers/llm/{key}",
        json={"provider": "azureOpenAI", "configuration": {**_public_configuration(), "apiKey": "spec-audit-wrong-key"}},
        timeout=HEALTH_CHECK_TIMEOUT,
    )

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["status"] == "error"
    assert _stored(config_client, key) == before


def test_a_key_of_another_type_is_a_400(config_client: ConfigClient, seed_llm_provider: SeedLlmProvider) -> None:
    key = seed_llm_provider()["modelKey"]

    resp = config_client.put(f"/ai-models/providers/embedding/{key}", json=VALID_BODY)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"status": "error", "message": f"Model key '{key}' belongs to type 'llm', not 'embedding'"}


def test_an_unknown_key_is_an_internal_error(config_client: ConfigClient) -> None:
    # API bug: the lookup walks every top-level key of the stored config, modelRoles (an
    # object, not a list) included, and throws before it can answer 404.
    resp = config_client.put(f"/ai-models/providers/llm/{MISSING_AI_MODEL_KEY}", json=VALID_BODY)

    assert resp.status_code == 500, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_INTERNAL_SERVER_ERROR"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({"configuration": {"model": "gpt"}}, "body.provider", id="provider-missing"),
        pytest.param({"provider": " ", "configuration": {"model": "gpt"}}, "body.provider", id="provider-blank"),
        pytest.param({"provider": "azureOpenAI"}, "body.configuration", id="configuration-missing"),
        pytest.param({**VALID_BODY, "isReasoning": "true"}, "body.isReasoning", id="is-reasoning-not-boolean"),
        pytest.param({**VALID_BODY, "contextLength": "8k"}, "body.contextLength", id="context-length-string"),
        pytest.param(
            {"provider": "azureOpenAI", "configuration": {"model": "a,b", "modelFriendlyName": "Two"}},
            "body.configuration.modelFriendlyName",
            id="friendly-name-with-several-models",
        ),
        pytest.param(
            {"provider": "azureOpenAI", "configuration": {"model": "gpt", "defaultReasoningEffort": "HIGH"}},
            "body.configuration.defaultReasoningEffort",
            id="reasoning-effort-wrong-case",
        ),
    ],
)
def test_invalid_body_is_rejected(config_client: ConfigClient, body: dict[str, Any], field: str) -> None:
    resp = config_client.put(f"/ai-models/providers/llm/{MISSING_AI_MODEL_KEY}", json=body)

    assert_validation_error(resp, field)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_model_type_is_rejected(config_client: ConfigClient) -> None:
    resp = config_client.put(f"/ai-models/providers/spec-audit-type/{MISSING_AI_MODEL_KEY}", json=VALID_BODY)

    assert_validation_error(resp, "params.modelType")
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("headers", [None, INVALID_BEARER_HEADERS], ids=["no-token", "invalid-token"])
def test_unauthenticated_is_rejected(config_client: ConfigClient, headers: dict[str, str] | None) -> None:
    resp = config_client.put(
        f"/ai-models/providers/llm/{MISSING_AI_MODEL_KEY}", auth=False, headers=headers, json=VALID_BODY
    )

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "PUT", f"/ai-models/providers/llm/{MISSING_AI_MODEL_KEY}", json=VALID_BODY)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_config_write_scope_is_forbidden(
    config_client: ConfigClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = config_client.put(
        f"/ai-models/providers/llm/{MISSING_AI_MODEL_KEY}", auth=False, headers=narrow_scope_headers, json=VALID_BODY
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_trust_remote_code_is_accepted_in_the_configuration(
    config_client: ConfigClient, seed_llm_provider: SeedLlmProvider
) -> None:
    key = seed_llm_provider()["modelKey"]

    resp = config_client.put(
        f"/ai-models/providers/llm/{key}",
        json={"provider": "azureOpenAI", "configuration": {**_public_configuration(), "trustRemoteCode": False}},
        timeout=HEALTH_CHECK_TIMEOUT,
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
