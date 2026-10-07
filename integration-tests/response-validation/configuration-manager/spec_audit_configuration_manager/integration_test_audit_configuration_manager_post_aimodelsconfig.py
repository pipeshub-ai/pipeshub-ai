"""Strict OpenAPI audit of POST /api/v1/configurationManager/aiModelsConfig.

Chain: authenticate -> requireScopes(config:write) -> userAdminCheck -> zod body ->
createAIModelsConfig, which health-checks the LLM and embedding entries through the query
service before it replaces the organization's whole AI model configuration.

A success replaces the models every other suite chats and indexes with, and fires the
"LLM / embedding configured" events, so it is not run here. Every case below stops before
anything is stored: the entries name a provider that does not exist.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    KV_AI_MODELS,
    assert_validation_error,
    read_stored_value,
    request_as,
    write_stored_value,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/aiModelsConfig"
AVAILABLE_ROUTE = "/api/v1/configurationManager/ai-models/available/:modelType"
PATH = "/aiModelsConfig"

UNKNOWN_PROVIDER_ENTRY: dict[str, Any] = {
    "provider": "spec-audit-no-such-provider",
    "configuration": {"model": "spec-audit-model"},
}
# Refused by the validator, so it can never reach the store even if a gate is missing.
EMPTY_MODELS_BODY: dict[str, Any] = {"llm": [], "embedding": []}


def _stored(config_client: ConfigClient) -> dict[str, Any]:
    resp = config_client.get(PATH)
    assert resp.status_code == 200, resp.text[:500]
    body: dict[str, Any] = resp.json()
    return body


@pytest.mark.parametrize(
    ("body", "message"),
    [
        pytest.param(
            {"llm": [UNKNOWN_PROVIDER_ENTRY], "embedding": []},
            "Failed to do health check of llm configuration, check credentials again",
            id="llm-health-check-fails",
        ),
        pytest.param(
            {"llm": [], "embedding": [UNKNOWN_PROVIDER_ENTRY]},
            "Failed to do health check of embedding configuration, check credentials again",
            id="embedding-health-check-fails",
        ),
    ],
)
def test_a_model_that_fails_its_health_check_is_an_internal_error_and_nothing_is_stored(
    config_client: ConfigClient, body: dict[str, Any], message: str
) -> None:
    before = _stored(config_client)

    resp = config_client.post(PATH, json=body)

    assert resp.status_code == 500, resp.text[:500]
    error = resp.json()["error"]
    assert (error["code"], error["message"]) == ("HTTP_INTERNAL_SERVER_ERROR", message)
    assert isinstance(error["metadata"], dict), error
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored(config_client) == before


@pytest.mark.parametrize(
    ("body", "model_type"),
    [
        pytest.param({"ocr": [UNKNOWN_PROVIDER_ENTRY]}, "ocr", id="only-ocr"),
        pytest.param({"slm": [UNKNOWN_PROVIDER_ENTRY], "llm": []}, "slm", id="no-embedding-key"),
    ],
)
def test_a_body_without_llm_or_embedding_replaces_the_whole_stored_config(
    config_client: ConfigClient, body: dict[str, Any], model_type: str
) -> None:
    # The validator accepts any one model type, and the stored config is replaced
    # as a whole: the chat and embedding models the body leaves out are dropped.
    raw = read_stored_value(KV_AI_MODELS)
    # The embedding guard refuses to put a dropped embedding model back through
    # the API while the vector store holds its vectors, so restore the stored bytes.
    try:
        resp = config_client.post(PATH, json=body)

        assert resp.status_code == 200, resp.text[:500]
        assert resp.json() == {"message": "AI config created successfully"}
        assert_strict_openapi_exchange(resp, ROUTE)
        stored = _stored(config_client)
        assert not stored.get("llm") and not stored.get("embedding"), stored

        # Only llm and embedding entries are given a modelKey on this route.
        available = config_client.get(f"/ai-models/available/{model_type}")
        assert available.status_code == 200, available.text[:500]
        assert_strict_openapi_exchange(available, AVAILABLE_ROUTE)
        models = available.json()["models"]
        assert [m["provider"] for m in models] == [UNKNOWN_PROVIDER_ENTRY["provider"]], models
        assert "modelKey" not in models[0], models
    finally:
        if raw is not None and read_stored_value(KV_AI_MODELS) != raw:
            write_stored_value(KV_AI_MODELS, raw)


def test_unknown_configuration_keys_pass_the_validator(config_client: ConfigClient) -> None:
    # The configuration object is passed through so registry-defined keys reach the health check.
    with outside_request_contract("specAuditKey is not a documented configuration key"):
        resp = config_client.post(
            PATH,
            json={
                "llm": [{**UNKNOWN_PROVIDER_ENTRY, "configuration": {"model": "m", "specAuditKey": "x"}}],
                "embedding": [],
            },
        )

    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["message"].startswith("Failed to do health check of llm")
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        pytest.param(EMPTY_MODELS_BODY, ["body"], id="no-model-configured"),
        pytest.param({}, ["body"], id="empty-object"),
        # The body object is strict: only the nine model-type keys are accepted.
        pytest.param({"llm": [UNKNOWN_PROVIDER_ENTRY], "embedding": [], "vision": []}, ["body"], id="unknown-model-type"),
        pytest.param(
            {
                "llm": [
                    {
                        "provider": "openAI",
                        "configuration": {"model": "spec-audit-a, spec-audit-b", "modelFriendlyName": "Spec Audit"},
                    }
                ],
                "embedding": [],
            },
            ["body.llm.0.configuration.modelFriendlyName"],
            id="friendly-name-with-several-models",
        ),
        pytest.param(
            {"llm": [{"provider": "openAI", "configuration": {"modelFriendlyName": "Spec Audit"}}], "embedding": []},
            ["body.llm.0.configuration.modelFriendlyName"],
            id="friendly-name-without-model",
        ),
        pytest.param({"llm": [{"provider": "", "configuration": {}}]}, ["body.llm.0.provider"], id="empty-provider"),
        pytest.param({"llm": [{"configuration": {}}]}, ["body.llm.0.provider"], id="missing-provider"),
        pytest.param({"llm": [{"provider": "openAI"}]}, ["body.llm.0.configuration"], id="missing-configuration"),
        pytest.param(
            {"llm": [{**UNKNOWN_PROVIDER_ENTRY, "isDefault": "no", "contextLength": "x"}]},
            ["body.llm.0.isDefault", "body.llm.0.contextLength"],
            id="wrong-types",
        ),
        pytest.param(
            {"llm": [{**UNKNOWN_PROVIDER_ENTRY, "configuration": {"model": "m", "defaultReasoningEffort": "extreme"}}]},
            ["body.llm.0.configuration.defaultReasoningEffort"],
            id="unknown-reasoning-effort",
        ),
        pytest.param({"llm": UNKNOWN_PROVIDER_ENTRY}, ["body.llm"], id="llm-not-a-list"),
    ],
)
def test_create_ai_models_config_rejects_invalid_body(
    config_client: ConfigClient, body: dict[str, Any], fields: list[str]
) -> None:
    resp = config_client.post(PATH, json=body)

    assert_validation_error(resp, *fields)
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("headers", [None, INVALID_BEARER_HEADERS], ids=["no-token", "invalid-token"])
def test_create_ai_models_config_without_valid_token_is_unauthorized(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.post(PATH, auth=False, headers=headers, json=EMPTY_MODELS_BODY)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_ai_models_config_as_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", PATH, json=EMPTY_MODELS_BODY)

    assert resp.status_code == 403, resp.text[:500]
    # The member is refused before the validator, so this empty body is only judged by the spec.
    with outside_request_contract("the empty lists are refused by the validator, which never runs here"):
        assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_config_write_scope_is_forbidden(config_client: ConfigClient, narrow_scope_headers: dict[str, str]) -> None:
    resp = config_client.post("/aiModelsConfig", auth=False, headers=narrow_scope_headers, json={})

    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
