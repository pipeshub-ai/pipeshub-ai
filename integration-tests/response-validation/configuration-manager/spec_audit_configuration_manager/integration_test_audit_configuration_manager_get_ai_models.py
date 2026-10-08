"""Strict OpenAPI audit of GET /api/v1/configurationManager/ai-models.

Chain: authenticate -> requireScopes(config:read) -> userAdminCheck -> getAIModelsProviders.
Every model-type list and ``modelRoles`` are always present (filled in when not stored), and
each entry's configuration is cut down to the public allowlist.
"""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/ai-models"
PATH = "/ai-models"
MODEL_TYPES = {"ocr", "embedding", "slm", "llm", "reasoning", "multiModal", "imageGeneration", "tts", "stt"}
PUBLIC_CONFIGURATION_KEYS = {"model", "modelFriendlyName", "dimensions", "defaultReasoningEffort"}


def test_admin_lists_every_model_type_without_provider_secrets(config_client: ConfigClient) -> None:
    resp = config_client.get(PATH)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["status"] == "success"
    # The stack is configured with an LLM and an embedding model, so the stored branch answers.
    assert body["message"] == "AI models retrieved successfully"
    models = body["models"]
    assert MODEL_TYPES <= set(models), sorted(models)
    assert isinstance(models["modelRoles"], dict)
    assert models["llm"] and models["embedding"], "the stack has no LLM or embedding model configured"
    for model_type in MODEL_TYPES:
        for entry in models[model_type]:
            assert set(entry["configuration"]) <= PUBLIC_CONFIGURATION_KEYS, entry
    # POST /aiModelsConfig keys only llm and embedding entries, so other types may lack modelKey.
    for entry in models["llm"] + models["embedding"]:
        assert isinstance(entry["modelKey"], str) and entry["modelKey"], entry


def test_the_stored_entries_are_the_same_as_in_ai_models_config(config_client: ConfigClient) -> None:
    resp = config_client.get(PATH)
    stored = config_client.get("/aiModelsConfig")

    assert resp.status_code == 200 and stored.status_code == 200, (resp.text[:300], stored.text[:300])
    assert_strict_openapi_exchange(resp, ROUTE)
    for model_type in MODEL_TYPES:
        assert resp.json()["models"][model_type] == stored.json().get(model_type, []), model_type


@pytest.mark.parametrize("headers", [None, INVALID_BEARER_HEADERS], ids=["no-token", "invalid-token"])
def test_without_valid_token_is_unauthorized(config_client: ConfigClient, headers: dict[str, str] | None) -> None:
    resp = config_client.get(PATH, auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", PATH)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_config_read_scope_is_forbidden(config_client: ConfigClient, narrow_scope_headers: dict[str, str]) -> None:
    resp = config_client.get("/ai-models", auth=False, headers=narrow_scope_headers)

    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
