"""The chat model an eval talks to, chosen from the environment.

Kept apart from ``live_runner`` so a caller that only needs a model (the demo
answer judge, its calibration run, the demo integration test) does not import
the agent runtime.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from app.config.constants.ai_models import AzureOpenAILLM
from app.connectors.sources.demo.harness.answer_judge import LangChainJudgeClient

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel


class MissingModelError(RuntimeError):
    """No model to run against — a run without one measures nothing."""


def build_chat_model(provider: str, model: str, api_key: str | None) -> BaseChatModel:
    """A LangChain chat model for ``provider``."""
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

        endpoint = os.getenv("TEST_AZURE_OPENAI_ENDPOINT")
        deployment = os.getenv("TEST_AZURE_OPENAI_DEPLOYMENT_NAME")
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
            api_version=AzureOpenAILLM.AZURE_OPENAI_VERSION.value,
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


def judge_client_from_env() -> tuple[LangChainJudgeClient, str, str]:
    """The demo answer judge's client, provider and model.

    Azure OpenAI when its key is set, as the integration workflow's instance
    uses; ``EVAL_PROVIDER`` overrides. Raises ``MissingModelError`` when the
    provider it settles on is not fully configured.
    """
    default = "azure_openai" if os.getenv("TEST_AZURE_OPENAI_API_KEY") else "openai"
    provider, model, key = resolve_model(provider_override=os.getenv("EVAL_PROVIDER") or default)
    chat = build_chat_model(provider, model, key)
    return LangChainJudgeClient(chat, json_mode=provider in ("openai", "azure_openai")), provider, model


__all__ = ["MissingModelError", "build_chat_model", "judge_client_from_env", "resolve_model"]
