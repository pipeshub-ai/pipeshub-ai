"""Strict OpenAPI audit of GET /api/v1/configurationManager/ai-models/:modelType."""

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

ROUTE = "/api/v1/configurationManager/ai-models/:modelType"
MODEL_TYPES = ["llm", "embedding", "ocr", "slm", "reasoning", "multiModal", "imageGeneration", "tts", "stt"]
# What every read keeps of a stored configuration; credentials and endpoints are left out.
PUBLIC_CONFIGURATION_KEYS = {"model", "modelFriendlyName", "dimensions", "defaultReasoningEffort"}


@pytest.mark.parametrize("model_type", MODEL_TYPES)
def test_admin_lists_the_stored_entries_of_a_type(config_client: ConfigClient, model_type: str) -> None:
    resp = config_client.get(f"/ai-models/{model_type}")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["status"] == "success"
    assert body["message"] in (
        f"Found {len(body['models'])} {model_type} models",
        f"No {model_type} models found",
    )
    for entry in body["models"]:
        assert set(entry["configuration"]) <= PUBLIC_CONFIGURATION_KEYS, entry


def test_a_new_entry_is_listed_without_its_credentials(
    config_client: ConfigClient, seed_llm_provider: SeedLlmProvider
) -> None:
    configuration = {"modelFriendlyName": "Spec audit model", "defaultReasoningEffort": "low"}
    seeded = seed_llm_provider(
        configuration={**azure_llm_configuration(), **configuration}, isReasoning=True, contextLength=4096
    )

    resp = config_client.get("/ai-models/llm")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    entry = next(m for m in resp.json()["models"] if m["modelKey"] == seeded["modelKey"])
    assert entry == {
        "provider": "azureOpenAI",
        "configuration": {"model": azure_llm_configuration()["model"], **configuration},
        "modelKey": seeded["modelKey"],
        "isMultimodal": False,
        "isDefault": False,
        "isReasoning": True,
        "contextLength": 4096,
        # The add route copies the friendly name next to the configuration too.
        "modelFriendlyName": "Spec audit model",
    }


def test_unknown_model_type_is_rejected(config_client: ConfigClient) -> None:
    resp = config_client.get("/ai-models/spec-audit-type")

    assert_validation_error(resp, "params.modelType")
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("headers", [None, INVALID_BEARER_HEADERS], ids=["no-token", "invalid-token"])
def test_unauthenticated_is_rejected(config_client: ConfigClient, headers: dict[str, str] | None) -> None:
    resp = config_client.get("/ai-models/llm", auth=False, headers=headers)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", "/ai-models/llm")

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_config_read_scope_is_forbidden(
    config_client: ConfigClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = config_client.get("/ai-models/llm", auth=False, headers=narrow_scope_headers)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

