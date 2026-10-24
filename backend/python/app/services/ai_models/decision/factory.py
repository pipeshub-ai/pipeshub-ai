"""Build a decision model client from a stored ``aiModels.decision`` entry."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.services.ai_models.decision.clients import (
    AzureOpenAIDecisionModel,
    DecisionModel,
    OpenAIDecisionModel,
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


_DECISION_BUILDERS: dict[str, Callable[[Mapping[str, Any], str], DecisionModel]] = {
    "openAI": _openai,
    "azureOpenAI": _azure_openai,
    "typesafe": _typesafe,
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
