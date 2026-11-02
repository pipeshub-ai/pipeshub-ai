"""Tests for AIModelsClient request timeouts."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from helper import ai_models_setup
from helper.clients.ai_models_client import PROVIDER_HEALTH_CHECK_TIMEOUT_S, AIModelsClient

pytestmark = pytest.mark.unit

_AI_SERVICE_COMMAND = (
    Path(__file__).resolve().parents[2]
    / "backend/nodejs/apps/src/libs/commands/ai_service/ai.service.command.ts"
)


def test_adding_a_provider_waits_for_its_health_check() -> None:
    http = MagicMock()
    AIModelsClient(http).add_provider(providerId="azureOpenAI", modelType="llm")
    http.request.assert_called_once_with(
        "POST",
        "/api/v1/configurationManager/ai-models/providers",
        auth=True,
        json={"providerId": "azureOpenAI", "modelType": "llm"},
        timeout=PROVIDER_HEALTH_CHECK_TIMEOUT_S,
    )


def test_updating_a_provider_waits_for_its_health_check() -> None:
    http = MagicMock()
    AIModelsClient(http).update_provider("llm", "key-1", isDefault=True)
    http.request.assert_called_once_with(
        "PUT",
        "/api/v1/configurationManager/ai-models/providers/llm/key-1",
        auth=True,
        json={"isDefault": True},
        timeout=PROVIDER_HEALTH_CHECK_TIMEOUT_S,
    )


def test_the_client_outwaits_the_server() -> None:
    match = re.search(r"AI_SERVICE_TIMEOUT_MS = ([\d_]+);", _AI_SERVICE_COMMAND.read_text())
    assert match, f"AI_SERVICE_TIMEOUT_MS not found in {_AI_SERVICE_COMMAND}"
    server_ms = int(match.group(1).replace("_", ""))
    assert PROVIDER_HEALTH_CHECK_TIMEOUT_S * 1000 > server_ms
    assert ai_models_setup.PROVIDER_HEALTH_CHECK_TIMEOUT_S * 1000 > server_ms
