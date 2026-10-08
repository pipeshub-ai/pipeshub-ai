"""Strict OpenAPI audit of DELETE /api/v1/configurationManager/ai-models/providers/:modelType/:modelKey.

Only llm entries this test added are deleted (the run's own Azure model, never the default).
"""

from __future__ import annotations

import uuid

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    MISSING_AI_MODEL_KEY,
    SeedLlmProvider,
    assert_validation_error,
    azure_llm_configuration,
    request_as,
)
from helper.clients.agents_client import AgentsClient
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/ai-models/providers/:modelType/:modelKey"


def _stored_llm_keys(config_client: ConfigClient) -> set[str]:
    resp = config_client.get("/ai-models/llm")
    assert resp.status_code == 200, resp.text[:500]
    return {m["modelKey"] for m in resp.json()["models"]}


def test_admin_deletes_an_entry_no_agent_uses(
    config_client: ConfigClient, seed_llm_provider: SeedLlmProvider
) -> None:
    key = seed_llm_provider(contextLength=4096)["modelKey"]

    resp = config_client.delete(f"/ai-models/providers/llm/{key}", timeout=120)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "status": "success",
        "message": "LLM provider deleted successfully",
        "details": {
            "modelKey": key,
            "modelType": "llm",
            "provider": "azureOpenAI",
            "model": azure_llm_configuration()["model"],
            "wasDefault": False,
            "contextLength": 4096,
        },
    }
    assert key not in _stored_llm_keys(config_client)


def test_an_entry_an_agent_uses_cannot_be_deleted(
    config_client: ConfigClient, agents_client: AgentsClient, seed_llm_provider: SeedLlmProvider
) -> None:
    key = seed_llm_provider(isReasoning=True, configuration={**azure_llm_configuration(), "modelFriendlyName": "Spec audit in use"})["modelKey"]
    agent_name = f"spec-audit-model-user-{uuid.uuid4().hex[:8]}"
    created = agents_client.create_agent(
        name=agent_name,
        models=[{"modelKey": key, "modelName": azure_llm_configuration()["model"], "provider": "azureOpenAI", "isReasoning": True}],
    )
    assert created.status_code == 201, created.text[:500]
    agent_key = created.json()["agent"]["_key"]
    try:
        resp = config_client.delete(f"/ai-models/providers/llm/{key}", timeout=120)

        assert resp.status_code == 409, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        error = resp.json()["error"]
        assert error["code"] == "HTTP_CONFLICT"
        assert error["message"] == (
            f"Cannot delete model 'Spec audit in use': currently in use by agent '{agent_name}'. "
            "Remove it from the agent first."
        )
        assert key in _stored_llm_keys(config_client)
    finally:
        deleted = agents_client.delete_agent(agent_key)
        assert deleted.status_code in (200, 204, 404), deleted.text[:300]


def test_a_key_of_another_type_is_a_400(config_client: ConfigClient, seed_llm_provider: SeedLlmProvider) -> None:
    key = seed_llm_provider()["modelKey"]

    resp = config_client.delete(f"/ai-models/providers/embedding/{key}")

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"status": "error", "message": f"Model key '{key}' belongs to type 'llm', not 'embedding'"}
    assert key in _stored_llm_keys(config_client)


def test_an_unknown_key_is_not_found(config_client: ConfigClient) -> None:
    resp = config_client.delete(f"/ai-models/providers/llm/{MISSING_AI_MODEL_KEY}")

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"status": "error", "message": f"Model with key '{MISSING_AI_MODEL_KEY}' not found"}


def test_unknown_model_type_is_rejected(config_client: ConfigClient) -> None:
    resp = config_client.delete(f"/ai-models/providers/spec-audit-type/{MISSING_AI_MODEL_KEY}")

    assert_validation_error(resp, "params.modelType")
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("headers", [None, INVALID_BEARER_HEADERS], ids=["no-token", "invalid-token"])
def test_unauthenticated_is_rejected(config_client: ConfigClient, headers: dict[str, str] | None) -> None:
    resp = config_client.delete(f"/ai-models/providers/llm/{MISSING_AI_MODEL_KEY}", auth=False, headers=headers)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "DELETE", f"/ai-models/providers/llm/{MISSING_AI_MODEL_KEY}")

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_config_write_scope_is_forbidden(
    config_client: ConfigClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = config_client.delete(
        f"/ai-models/providers/llm/{MISSING_AI_MODEL_KEY}", auth=False, headers=narrow_scope_headers
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
