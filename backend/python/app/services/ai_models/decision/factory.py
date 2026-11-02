"""Build a decision model client from a stored ``aiModels.decision`` entry."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.services.ai_models.decision.clients import (
    AzureOpenAIDecisionModel,
    DecisionModel,
    OpenAIDecisionModel,
    OpenRouterDecisionModel,
    TypeSafeDecisionModel,
)
from app.utils.aimodels import require_allowed_endpoint, select_model_name

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


def _endpoint(configuration: Mapping[str, Any]) -> str | None:
    endpoint = configuration.get("endpoint")
    return endpoint.strip() or None if isinstance(endpoint, str) else None


def _openai(configuration: Mapping[str, Any], model_name: str) -> DecisionModel:
    return OpenAIDecisionModel(
        model_name,
        api_key=configuration["apiKey"],
        base_url=_endpoint(configuration),
        organization=configuration.get("organizationId") or None,
    )


def _azure_openai(configuration: Mapping[str, Any], model_name: str) -> DecisionModel:
    endpoint = _endpoint(configuration)
    if not endpoint:
        raise ValueError("Azure OpenAI decision models need an endpoint")
    # Azure addresses the deployment, which may be named differently from the model.
    deployment = (configuration.get("deploymentName") or "").strip() or model_name
    return AzureOpenAIDecisionModel(deployment, api_key=configuration["apiKey"], endpoint=endpoint)


def _typesafe(configuration: Mapping[str, Any], model_name: str) -> DecisionModel:
    return TypeSafeDecisionModel(
        model_name, api_key=configuration["apiKey"], base_url=_endpoint(configuration)
    )


def _openrouter(configuration: Mapping[str, Any], model_name: str) -> DecisionModel:
    return OpenRouterDecisionModel(model_name, api_key=configuration["apiKey"])


def _openai_gateway(provider: str, label: str) -> Callable[[Mapping[str, Any], str], DecisionModel]:
    """A gateway that serves the OpenAI Decisions API at its own endpoint."""

    def build(configuration: Mapping[str, Any], model_name: str) -> DecisionModel:
        endpoint = _endpoint(configuration)
        if not endpoint:
            raise ValueError(f"{label} decision models need an endpoint")
        return OpenAIDecisionModel(
            model_name, api_key=configuration["apiKey"], base_url=endpoint, provider=provider
        )

    return build


_DECISION_BUILDERS: dict[str, Callable[[Mapping[str, Any], str], DecisionModel]] = {
    "openAI": _openai,
    "azureOpenAI": _azure_openai,
    "typesafe": _typesafe,
    "openRouter": _openrouter,
    "litellmProxy": _openai_gateway("litellmProxy", "LiteLLM Proxy"),
    "openAICompatible": _openai_gateway("openAICompatible", "OpenAI Compatible"),
}


def get_decision_model(provider: str, config: dict, model_name: str | None = None) -> DecisionModel:
    configuration = config["configuration"]
    require_allowed_endpoint(_endpoint(configuration))
    model_name = select_model_name(
        configuration,
        model_name,
        is_default=config.get("isDefault"),
        empty_error="No decision model configured",
    )
    builder = _DECISION_BUILDERS.get(provider)
    if builder is None:
        raise ValueError(f"Unsupported decision model provider: {provider}")
    return builder(configuration, model_name)
