"""Decision models in the provider registry, discovery, the health check and config lookup."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

import app.config.ai_models.providers  # noqa: F401  -- populate the registry
from app.api.routes.health import (
    SUPPORTED_HEALTH_CHECK_TYPES,
    perform_decision_health_check,
)
from app.config.ai_models.registry import ai_model_registry
from app.config.ai_models.types import CAPABILITY_TO_MODEL_TYPE
from app.services.ai_models.decision import (
    DecisionAnswer,
    DecisionModelError,
    DecisionResult,
)
from app.services.ai_models.discovery.classifier import classify
from app.services.ai_models.discovery.registry import discovery_registry
from app.services.ai_models.discovery.service import _for_capability
from app.services.ai_models.discovery.types import (
    DiscoveredModel,
    DiscoveryResult,
    RawModel,
)
from app.utils.llm import get_decision_config, get_decision_model_instance


def _field_names(provider_id: str) -> list[str]:
    provider = ai_model_registry.get_provider(provider_id)
    return [field["name"] for field in provider["fields"]["decision"]]


def test_registry_lists_decision_providers() -> None:
    assert CAPABILITY_TO_MODEL_TYPE["decision"] == "decision"
    ids = {p["providerId"] for p in ai_model_registry.list_providers() if "decision" in p["capabilities"]}
    assert ids == {"openAI", "azureOpenAI", "typesafe"}
    assert _field_names("typesafe") == ["apiKey", "endpoint", "model", "modelFriendlyName"]
    assert _field_names("azureOpenAI") == ["endpoint", "apiKey", "model", "deploymentName", "modelFriendlyName"]
    assert "apiKey" in _field_names("openAI")


async def test_discovery_lists_jev_for_typesafe_without_a_key() -> None:
    strategy = discovery_registry.get("typesafe")
    raw, _ = await strategy.list_models({}, None, None)
    assert [model.id for model in raw][:2] == ["jev-latest", "jev-preview"]
    assert all(model.capabilities == ["decision"] for model in raw)


def test_openai_luna_is_both_a_chat_and_a_decision_model() -> None:
    model = classify("openAI", RawModel(id="gpt-6-luna"))
    assert "decision" in model.capabilities
    other = classify("openAI", RawModel(id="gpt-6-sol"))
    assert "decision" not in other.capabilities
    dated = classify("openAI", RawModel(id="gpt-6-luna-2026-10-06", capabilities=["llm"]))
    assert dated.capabilities == ["llm", "decision"]
    foundry = classify("azureOpenAI", RawModel(id="Microsoft-Decision-1", capabilities=["llm"]))
    assert foundry.capabilities == ["decision"]


def test_decision_tab_drops_unclassified_models() -> None:
    def row(model_id: str, capabilities: list[str]) -> DiscoveredModel:
        return DiscoveredModel(id=model_id, display_name=model_id, capabilities=capabilities, source="heuristic")

    result = DiscoveryResult(
        supported=True,
        models=[row("gpt-6-luna", ["llm", "decision"]), row("mystery", ["other"]), row("gpt-6-sol", ["llm"])],
    )
    assert [m.id for m in _for_capability(result, "decision").models] == ["gpt-6-luna"]
    assert [m.id for m in _for_capability(result, "text_generation").models] == ["gpt-6-luna", "mystery", "gpt-6-sol"]


def _entry(**overrides) -> dict:
    return {
        "provider": "typesafe",
        "configuration": {"model": "jev-latest", "apiKey": "k"},
        "isDefault": True,
        **overrides,
    }


async def test_health_check_asks_one_question_and_reports_healthy() -> None:
    assert "decision" in SUPPORTED_HEALTH_CHECK_TYPES
    model = MagicMock()
    model.decide = AsyncMock(return_value=DecisionResult(
        model="jev-1.13.0",
        answers={"is_greeting": DecisionAnswer(name="is_greeting", type="predicate", probability=0.97)},
    ))
    with patch("app.api.routes.health.get_decision_model", return_value=model):
        response = await perform_decision_health_check(_entry(), MagicMock())
    assert response.status_code == 200
    assert json.loads(response.body)["status"] == "healthy"
    model.decide.assert_awaited_once()


async def test_health_check_reports_a_bad_key_as_a_settings_error() -> None:
    request = httpx.Request("POST", "https://api.typesafe.ai/v1/systemone")
    response_401 = httpx.Response(401, request=request)
    error = DecisionModelError("typesafe decision request returned HTTP 401: invalid key", status_code=401)
    error.__cause__ = httpx.HTTPStatusError("401", request=request, response=response_401)
    model = MagicMock()
    model.decide = AsyncMock(side_effect=error)
    with patch("app.api.routes.health.get_decision_model", return_value=model):
        response = await perform_decision_health_check(_entry(), MagicMock())
    assert response.status_code == 400
    assert json.loads(response.body)["details"]["error_code"] == "auth_error"


async def test_health_check_fails_when_the_response_has_no_answer() -> None:
    model = MagicMock()
    model.decide = AsyncMock(return_value=DecisionResult(
        model="x",
        answers={"is_greeting": DecisionAnswer(name="is_greeting", type="predicate", missing=True)},
    ))
    with patch("app.api.routes.health.get_decision_model", return_value=model):
        response = await perform_decision_health_check(_entry(), MagicMock())
    assert response.status_code == 400
    assert json.loads(response.body)["details"]["error_code"] == "invalid_response"
    assert model.decide.await_args.kwargs["max_attempts"] == 1


async def test_health_check_without_model_name_fails() -> None:
    response = await perform_decision_health_check(_entry(configuration={"apiKey": "k"}), MagicMock())
    assert response.status_code == 400
    assert json.loads(response.body)["details"]["error_code"] == "invalid_request"


async def test_config_lookup_prefers_the_default_decision_model() -> None:
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={
        "llm": [],
        "decision": [
            {"provider": "openAI", "configuration": {"model": "gpt-6-luna", "apiKey": "k"}, "isDefault": False},
            {"provider": "typesafe", "configuration": {"model": "jev-latest", "apiKey": "k"}, "isDefault": True},
        ],
    })
    config = await get_decision_config(config_service)
    assert config["provider"] == "typesafe"
    model, _ = await get_decision_model_instance(config_service)
    assert model.model_name == "jev-latest"


async def test_config_lookup_without_decision_models_returns_none() -> None:
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={"llm": []})
    assert await get_decision_model_instance(config_service) is None
