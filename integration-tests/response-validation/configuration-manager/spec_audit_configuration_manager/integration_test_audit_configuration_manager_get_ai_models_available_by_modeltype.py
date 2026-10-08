"""Strict OpenAPI audit of GET /api/v1/configurationManager/ai-models/available/:modelType.

No admin check: any member may list the models (the model pickers use it).
"""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    SeedLlmProvider,
    assert_validation_error,
    azure_llm_configuration,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/ai-models/available/:modelType"
MODEL_TYPES = ["llm", "embedding", "ocr", "slm", "reasoning", "multiModal", "imageGeneration", "tts", "stt"]


@pytest.mark.parametrize("model_type", MODEL_TYPES)
def test_admin_lists_the_flattened_models_of_a_type(config_client: ConfigClient, model_type: str) -> None:
    resp = config_client.get(f"/ai-models/available/{model_type}")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["status"] == "success"
    assert all(model["modelType"] == model_type for model in body["models"])
    assert body["message"] in (
        f"Found {len(body['models'])} {model_type} models",
        f"No {model_type} models found",
    )


def test_an_entry_with_several_models_is_expanded_into_one_item_each(
    config_client: ConfigClient, second_user: SecondUser, seed_llm_provider: SeedLlmProvider
) -> None:
    azure = azure_llm_configuration()
    seeded = seed_llm_provider(
        configuration={**azure, "defaultReasoningEffort": "high"}, isReasoning=True, isMultimodal=True
    )
    single = seed_llm_provider(configuration={**azure, "modelFriendlyName": "Spec audit single"})

    resp = request_as(second_user, "GET", "/ai-models/available/llm")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    by_key = {}
    for model in resp.json()["models"]:
        by_key.setdefault(model["modelKey"], []).append(model)
    assert by_key[seeded["modelKey"]] == [
        {
            "modelType": "llm",
            "provider": "azureOpenAI",
            "modelName": azure["model"],
            "modelKey": seeded["modelKey"],
            "isMultimodal": True,
            "isReasoning": True,
            "isDefault": False,
            "defaultReasoningEffort": "high",
        }
    ]
    assert by_key[single["modelKey"]][0]["modelFriendlyName"] == "Spec audit single"


def test_unknown_model_type_is_rejected(config_client: ConfigClient) -> None:
    resp = config_client.get("/ai-models/available/spec-audit-type")

    assert_validation_error(resp, "params.modelType")
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("headers", [None, INVALID_BEARER_HEADERS], ids=["no-token", "invalid-token"])
def test_unauthenticated_is_rejected(config_client: ConfigClient, headers: dict[str, str] | None) -> None:
    resp = config_client.get("/ai-models/available/llm", auth=False, headers=headers)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_config_read_scope_is_forbidden(
    config_client: ConfigClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = config_client.get("/ai-models/available/llm", auth=False, headers=narrow_scope_headers)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
