"""The chat model an eval talks to, chosen from the environment.

Kept apart from ``live_runner`` so a caller that only needs a model (the demo
answer judge, its calibration run, the demo integration test) does not import
the agent runtime.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from app.config.constants.ai_models import AzureOpenAILLM
from app.connectors.sources.demo.harness.answer_judge import (
    AnthropicJudgeClient,
    LangChainJudgeClient,
)

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel


class MissingModelError(RuntimeError):
    """No model to run against — a run without one measures nothing."""


def build_chat_model(
    provider: str,
    model: str,
    api_key: str | None,
    *,
    azure_endpoint: str | None = None,
    azure_deployment: str | None = None,
    azure_api_version: str | None = None,
) -> BaseChatModel:
    """A LangChain chat model for ``provider``.

    Azure's endpoint and deployment default to ``TEST_AZURE_OPENAI_*``; the
    demo answer judge passes its own.
    """
    if not api_key:
        raise MissingModelError(
            f"No API key for '{provider}'. Set the key in the workflow's "
            "environment (TEST_AZURE_OPENAI_API_KEY for Azure OpenAI, "
            "TEST_OPENAI_API_KEY for OpenAI) and run again."
        )
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, api_key=api_key, temperature=0)
    if provider == "azure_openai":
        from langchain_openai import AzureChatOpenAI

        endpoint = azure_endpoint or os.getenv("TEST_AZURE_OPENAI_ENDPOINT")
        deployment = azure_deployment or os.getenv("TEST_AZURE_OPENAI_DEPLOYMENT_NAME")
        if not endpoint or not deployment:
            raise MissingModelError(
                "Azure OpenAI needs an endpoint and a deployment as well as a "
                "key. Set TEST_AZURE_OPENAI_ENDPOINT and "
                "TEST_AZURE_OPENAI_DEPLOYMENT_NAME and run again."
            )
        return AzureChatOpenAI(
            model=model,
            api_key=api_key,
            azure_endpoint=endpoint,
            azure_deployment=deployment,
            # The product's own version, so the evals talk to Azure the way the
            # thing they are measuring does.
            api_version=azure_api_version or AzureOpenAILLM.AZURE_OPENAI_VERSION.value,
            temperature=0,
        )
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, api_key=api_key, temperature=0)
    raise MissingModelError(
        f"'{provider}' is not a provider this runner knows. Use 'azure_openai', "
        "'openai' or 'anthropic', or add it to build_chat_model in "
        "tests/evals/chat_models.py."
    )


def resolve_model(
    provider_override: str | None = None, model_override: str | None = None
) -> tuple[str, str, str | None]:
    """Provider, model and key — the key chosen for the FINAL provider.

    The provider must be settled before its key is read. Picking the key first
    and then letting a command-line flag change the provider would send one
    provider's key to another's endpoint: an authentication failure, and a
    secret handed to a party that should never see it.

    Reads the variables the integration workflows already set, so a scheduled
    run needs no new secret.
    """
    provider = provider_override or os.getenv("EVAL_PROVIDER", "openai")
    model = model_override or os.getenv("EVAL_MODEL") or ""
    if provider == "azure_openai":
        return (
            provider,
            model
            or os.getenv("TEST_AZURE_OPENAI_MODEL")
            or os.getenv("TEST_AZURE_OPENAI_DEPLOYMENT_NAME")
            or "",
            os.getenv("TEST_AZURE_OPENAI_API_KEY"),
        )
    if provider == "openai":
        return provider, model or os.getenv("TEST_OPENAI_LLM_MODEL") or "gpt-4o-mini", os.getenv(
            "TEST_OPENAI_API_KEY"
        )
    if provider == "anthropic":
        return provider, model or "claude-haiku-4-5", os.getenv("TEST_ANTHROPIC_API_KEY")
    # An unknown provider has no key here; build_chat_model says so by name
    # rather than trying whatever key happens to be set.
    return provider, model, None


JUDGE_PROVIDERS = ("azure_openai", "anthropic_foundry", "openai", "anthropic")

# Azure resource hosts whose first label is the resource name Foundry needs.
_AZURE_RESOURCE_HOST = re.compile(
    r"^(?P<resource>[a-z0-9][a-z0-9-]*)\.(?:cognitiveservices\.azure\.com|openai\.azure\.com|services\.ai\.azure\.com)$"
)


class JudgeConfigError(RuntimeError):
    """JUDGE_PROVIDER is set but the rest of the judge's settings are not usable."""


@dataclass(frozen=True)
class JudgeModel:
    client: LangChainJudgeClient | AnthropicJudgeClient
    provider: str
    model: str
    # True when the JUDGE_* settings chose it, rather than the eval/instance ones.
    dedicated: bool

    def describe(self) -> str:
        source = "dedicated JUDGE_* settings" if self.dedicated else "the eval settings"
        return f"{self.provider} / {self.model or 'default model'} ({source})"


def foundry_resource(endpoint: str) -> str | None:
    """The Azure resource name in an endpoint URL, or None when it isn't one."""
    host = (urlparse(endpoint if "//" in endpoint else f"//{endpoint}").hostname or "").lower()
    match = _AZURE_RESOURCE_HOST.match(host)
    return match.group("resource") if match else None


def build_foundry_judge_client(resource: str, api_key: str, model: str) -> AnthropicJudgeClient:
    """Claude on Azure AI Foundry, through the Anthropic Messages API."""
    try:
        from anthropic import AnthropicFoundry
    except ImportError as exc:
        raise JudgeConfigError(
            "the installed anthropic SDK has no AnthropicFoundry client; "
            "anthropic_foundry needs the version langchain-anthropic pins in backend/python/pyproject.toml."
        ) from exc
    return AnthropicJudgeClient(AnthropicFoundry(resource=resource, api_key=api_key, timeout=120), model)


def _foundry_judge(key: str | None, model: str) -> JudgeModel:
    model = model or os.getenv("JUDGE_AZURE_DEPLOYMENT") or ""
    endpoint = os.getenv("JUDGE_AZURE_ENDPOINT") or ""
    resource = os.getenv("JUDGE_FOUNDRY_RESOURCE") or (foundry_resource(endpoint) if endpoint else None)
    missing = [name for name, value in (("JUDGE_API_KEY", key), ("JUDGE_MODEL or JUDGE_AZURE_DEPLOYMENT", model)) if not value]
    if not resource:
        missing.append(
            "JUDGE_FOUNDRY_RESOURCE, or a JUDGE_AZURE_ENDPOINT on *.cognitiveservices.azure.com, "
            "*.openai.azure.com or *.services.ai.azure.com" + (" (the one set is not)" if endpoint else "")
        )
    if missing:
        raise JudgeConfigError(f"JUDGE_PROVIDER=anthropic_foundry but not set: {'; '.join(missing)}.")
    return JudgeModel(build_foundry_judge_client(resource, key or "", model), "anthropic_foundry", model, dedicated=True)


def _dedicated_judge(provider: str) -> JudgeModel:
    if provider not in JUDGE_PROVIDERS:
        raise JudgeConfigError(f"JUDGE_PROVIDER is '{provider}'; use one of {', '.join(JUDGE_PROVIDERS)}.")
    key = os.getenv("JUDGE_API_KEY")
    model = os.getenv("JUDGE_MODEL") or ""
    if provider == "anthropic_foundry":
        return _foundry_judge(key, model)
    endpoint = os.getenv("JUDGE_AZURE_ENDPOINT")
    deployment = os.getenv("JUDGE_AZURE_DEPLOYMENT")
    needed = {"JUDGE_API_KEY": key}
    if provider == "azure_openai":
        needed |= {"JUDGE_AZURE_ENDPOINT": endpoint, "JUDGE_AZURE_DEPLOYMENT": deployment}
        model = model or deployment or ""
    else:
        needed["JUDGE_MODEL"] = model
    missing = [name for name, value in needed.items() if not value]
    if missing:
        # No fallback to the answering model: that would quietly bring back the
        # self-preference the dedicated judge exists to avoid.
        raise JudgeConfigError(f"JUDGE_PROVIDER={provider} but not set: {', '.join(missing)}.")
    try:
        chat = build_chat_model(
            provider, model, key,
            azure_endpoint=endpoint,
            azure_deployment=deployment,
            azure_api_version=os.getenv("JUDGE_AZURE_API_VERSION") or None,
        )
    except MissingModelError as exc:
        raise JudgeConfigError(str(exc)) from exc
    return JudgeModel(LangChainJudgeClient(chat, json_mode=provider != "anthropic"), provider, model, dedicated=True)


def judge_model_from_env() -> JudgeModel:
    """The demo answer judge's model.

    With ``JUDGE_PROVIDER`` set, only the ``JUDGE_*`` settings are used, and an
    incomplete set raises ``JudgeConfigError``. Otherwise the judge uses Azure
    OpenAI when its key is set, as the integration workflow's instance does;
    ``EVAL_PROVIDER`` overrides, and an unconfigured provider raises
    ``MissingModelError``.
    """
    judge_provider = os.getenv("JUDGE_PROVIDER")
    if judge_provider:
        return _dedicated_judge(judge_provider)
    default = "azure_openai" if os.getenv("TEST_AZURE_OPENAI_API_KEY") else "openai"
    provider, model, key = resolve_model(provider_override=os.getenv("EVAL_PROVIDER") or default)
    chat = build_chat_model(provider, model, key)
    return JudgeModel(LangChainJudgeClient(chat, json_mode=provider != "anthropic"), provider, model, dedicated=False)


__all__ = [
    "JUDGE_PROVIDERS",
    "JudgeConfigError",
    "JudgeModel",
    "MissingModelError",
    "build_chat_model",
    "build_foundry_judge_client",
    "foundry_resource",
    "judge_model_from_env",
    "resolve_model",
]
