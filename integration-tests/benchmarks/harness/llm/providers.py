"""PipesHub provider ids -> LiteLLM routing and the env vars holding their keys.

Ids match `LLMProvider` in `backend/python/app/utils/aimodels.py`. Adding a
provider is one entry here.
"""

from __future__ import annotations

from dataclasses import dataclass

from benchmarks.harness.errors import ConfigError


@dataclass(frozen=True)
class ProviderSpec:
    pipeshub_id: str
    litellm_prefix: str
    key_envs: tuple[str, ...]
    # Azure-style providers also need an endpoint and an API version.
    api_base_envs: tuple[str, ...] = ()
    api_version_envs: tuple[str, ...] = ()


PROVIDERS: dict[str, ProviderSpec] = {
    spec.pipeshub_id: spec
    for spec in (
        ProviderSpec("openAI", "openai", ("TEST_OPENAI_API_KEY", "OPENAI_API_KEY")),
        ProviderSpec("anthropic", "anthropic", ("TEST_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")),
        ProviderSpec("gemini", "gemini", ("TEST_GEMINI_API_KEY", "GEMINI_API_KEY")),
        ProviderSpec(
            "azureOpenAI", "azure", ("TEST_AZURE_OPENAI_API_KEY", "AZURE_OPENAI_API_KEY"),
            api_base_envs=("TEST_AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_ENDPOINT"),
            api_version_envs=("TEST_AZURE_OPENAI_API_VERSION", "AZURE_OPENAI_API_VERSION"),
        ),
    )
}


def provider_spec(provider_id: str) -> ProviderSpec:
    spec = PROVIDERS.get(provider_id)
    if spec is None:
        raise ConfigError(f"provider {provider_id!r} is not supported; use one of {sorted(PROVIDERS)}")
    return spec
