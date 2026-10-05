"""Strict OpenAPI audit of POST /api/v1/configurationManager/aiModelsConfig.

Negative paths only: a successful call replaces the org's whole AI model
configuration after live LLM / embedding health checks.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/aiModelsConfig"

# Refused by aiModelsConfigSchema, so it can never reach the store even if a gate is missing.
EMPTY_MODELS_BODY: dict[str, Any] = {"llm": [], "embedding": []}


def test_create_ai_models_config_without_token_is_unauthorized(config_client: ConfigClient) -> None:
    resp = config_client.post("/aiModelsConfig", auth=False, json=EMPTY_MODELS_BODY)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_create_ai_models_config_as_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", "/aiModelsConfig", json=EMPTY_MODELS_BODY)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(EMPTY_MODELS_BODY, id="no-model-configured"),
        # The body object is strict: only the nine model-type keys are accepted.
        pytest.param({"llm": [], "embedding": [], "vision": []}, id="unknown-model-type"),
        # Fails the configuration refinement, before any health check is attempted.
        pytest.param(
            {
                "llm": [
                    {
                        "provider": "openAI",
                        "configuration": {
                            "model": "spec-audit-a, spec-audit-b",
                            "modelFriendlyName": "Spec Audit",
                        },
                    }
                ],
                "embedding": [],
            },
            id="friendly-name-with-several-models",
        ),
    ],
)
def test_create_ai_models_config_rejects_invalid_body(config_client: ConfigClient, body: dict[str, Any]) -> None:
    resp = config_client.post("/aiModelsConfig", json=body)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
