"""Strict OpenAPI audit of POST /api/v1/configurationManager/ai-models/providers.

Chain: authenticate -> requireScopes(config:write) -> userAdminCheck -> zod -> health check
in the query service -> save. Entries are added with the run's own Azure model and never
as the default; every one is deleted again.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    SeedLlmProvider,
    assert_validation_error,
    azure_llm_configuration,
    llm_provider_body,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/ai-models/providers"
PATH = "/ai-models/providers"
HEALTH_CHECK_TIMEOUT = 120


def _stored_llm_keys(config_client: ConfigClient) -> set[str]:
    resp = config_client.get("/ai-models/llm")
    assert resp.status_code == 200, resp.text[:500]
    return {m["modelKey"] for m in resp.json()["models"]}


def _forget(config_client: ConfigClient, model_key: str) -> None:
    resp = config_client.delete(f"/ai-models/providers/llm/{model_key}", timeout=HEALTH_CHECK_TIMEOUT)
    assert resp.status_code in (200, 404), resp.text[:300]


def test_add_an_llm_that_passes_its_health_check(config_client: ConfigClient) -> None:
    resp = config_client.post(PATH, json=llm_provider_body(contextLength=8192), timeout=HEALTH_CHECK_TIMEOUT)
    try:
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        body = resp.json()
        assert (body["status"], body["message"]) == ("success", "LLM provider added successfully")
        details = body["details"]
        uuid.UUID(details["modelKey"])
        assert details == {
            "modelKey": details["modelKey"],
            "modelType": "llm",
            "provider": "azureOpenAI",
            "model": azure_llm_configuration()["model"],
            "isDefault": False,
            "contextLength": 8192,
        }
        assert details["modelKey"] in _stored_llm_keys(config_client)
    finally:
        if resp.status_code == 200:
            _forget(config_client, resp.json()["details"]["modelKey"])


def test_flags_left_out_are_saved_as_false_and_context_length_is_left_out(
    config_client: ConfigClient, seed_llm_provider: SeedLlmProvider
) -> None:
    body = {"modelType": "llm", "provider": "azureOpenAI", "configuration": azure_llm_configuration()}

    resp = config_client.post(PATH, json=body, timeout=HEALTH_CHECK_TIMEOUT)
    try:
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert "contextLength" not in resp.json()["details"]
        assert resp.json()["details"]["isDefault"] is False
    finally:
        if resp.status_code == 200:
            _forget(config_client, resp.json()["details"]["modelKey"])


def test_unknown_fields_are_dropped_but_unknown_configuration_keys_are_passed_on(config_client: ConfigClient) -> None:
    # configurationSchema is .passthrough(): registry keys it does not declare are not checked at all.
    configuration = {**azure_llm_configuration(), "project": 5, "specAuditKey": "kept"}
    body = {**llm_provider_body(configuration=configuration), "specAuditTopLevel": True}

    with outside_request_contract("an unknown field, an unknown configuration key and a mistyped registry key"):
        resp = config_client.post(PATH, json=body, timeout=HEALTH_CHECK_TIMEOUT)
        assert_strict_openapi_exchange(resp, ROUTE)
    try:
        assert resp.status_code == 200, resp.text[:500]
    finally:
        if resp.status_code == 200:
            _forget(config_client, resp.json()["details"]["modelKey"])


def test_a_rejected_api_key_is_a_400_and_nothing_is_stored(config_client: ConfigClient) -> None:
    before = _stored_llm_keys(config_client)
    configuration = {**azure_llm_configuration(), "apiKey": "spec-audit-wrong-key"}

    resp = config_client.post(PATH, json=llm_provider_body(configuration=configuration), timeout=HEALTH_CHECK_TIMEOUT)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["status"] == "error"
    assert "API key" in error["message"]
    assert _stored_llm_keys(config_client) == before


def test_a_context_length_outside_the_usable_range_is_refused_by_the_health_check(config_client: ConfigClient) -> None:
    before = _stored_llm_keys(config_client)

    resp = config_client.post(PATH, json=llm_provider_body(contextLength=100), timeout=HEALTH_CHECK_TIMEOUT)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)
    assert resp.json()["error"]["message"].startswith("Context length 100 is not a usable window")
    assert _stored_llm_keys(config_client) == before


def test_a_provider_the_registry_does_not_know_is_a_500(config_client: ConfigClient) -> None:
    # API bug: an unknown provider id is the caller's mistake, yet the health check answers 500.
    resp = config_client.post(
        PATH, json=llm_provider_body(provider="spec-audit-no-such-provider"), timeout=HEALTH_CHECK_TIMEOUT
    )

    assert resp.status_code == 500, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["status"] == "error"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        pytest.param({"modelType": "spec-audit-type"}, "body.modelType", id="model-type-outside-enum"),
        pytest.param({"modelType": None}, "body.modelType", id="model-type-null"),
        pytest.param({"provider": ""}, "body.provider", id="provider-empty"),
        pytest.param({"provider": " "}, "body.provider", id="provider-blank"),
        pytest.param({"configuration": None}, "body.configuration", id="configuration-null"),
        pytest.param({"isMultimodal": "yes"}, "body.isMultimodal", id="is-multimodal-not-boolean"),
        pytest.param({"isDefault": 1}, "body.isDefault", id="is-default-not-boolean"),
        pytest.param({"contextLength": "4096"}, "body.contextLength", id="context-length-string"),
        pytest.param(
            {"configuration": {"model": "gpt-a, gpt-b", "modelFriendlyName": "Two models"}},
            "body.configuration.modelFriendlyName",
            id="friendly-name-with-several-models",
        ),
        pytest.param(
            {"configuration": {"modelFriendlyName": "No model"}},
            "body.configuration.modelFriendlyName",
            id="friendly-name-without-model",
        ),
        pytest.param(
            {"configuration": {"model": "gpt", "defaultReasoningEffort": "extreme"}},
            "body.configuration.defaultReasoningEffort",
            id="reasoning-effort-outside-enum",
        ),
        pytest.param({"configuration": {"model": 5}}, "body.configuration.model", id="model-not-a-string"),
    ],
)
def test_invalid_body_is_rejected_before_the_health_check(
    config_client: ConfigClient, overrides: dict[str, Any], field: str
) -> None:
    body = {"modelType": "llm", "provider": "azureOpenAI", "configuration": {"model": "gpt"}, **overrides}

    resp = config_client.post(PATH, json=body)

    assert_validation_error(resp, field)
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("missing", ["modelType", "provider", "configuration"])
def test_a_missing_required_field_is_rejected(config_client: ConfigClient, missing: str) -> None:
    body = {"modelType": "llm", "provider": "azureOpenAI", "configuration": {"model": "gpt"}}
    del body[missing]

    resp = config_client.post(PATH, json=body)

    assert_validation_error(resp, f"body.{missing}")
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("headers", [None, INVALID_BEARER_HEADERS], ids=["no-token", "invalid-token"])
def test_unauthenticated_is_rejected(config_client: ConfigClient, headers: dict[str, str] | None) -> None:
    resp = config_client.post(PATH, auth=False, headers=headers, json={"modelType": "llm"})

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", PATH, json={"modelType": "llm"})

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_config_write_scope_is_forbidden(
    config_client: ConfigClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = config_client.post(PATH, auth=False, headers=narrow_scope_headers, json={"modelType": "llm"})

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_trust_remote_code_is_a_configuration_key_of_the_registry(config_client: ConfigClient) -> None:
    configuration = {**azure_llm_configuration(), "trustRemoteCode": False}

    resp = config_client.post(PATH, json=llm_provider_body(configuration=configuration), timeout=HEALTH_CHECK_TIMEOUT)
    try:
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    finally:
        if resp.status_code == 200:
            _forget(config_client, resp.json()["details"]["modelKey"])
