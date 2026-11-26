"""P4.3 FA-1, FA-2: one provider-family table; a usage journaled with its reporter's own count (before
USAGE1) is read as the whole prompt whoever reported it."""

from __future__ import annotations

import pytest

from harness_core.models.families import (
    legacy_input_tokens,
    provider_family,
    usage_reporting,
)


@pytest.mark.parametrize(
    ("name", "family"),
    [
        # As PipesHub configures them (aiModels ``provider``).
        ("openAI", "openai"),
        ("openAICompatible", "openai"),
        ("azureOpenAI", "azure_direct"),
        ("anthropic", "anthropic"),
        ("gemini", "gemini"),
        ("vertexAI", "gemini"),
        ("ollama", "ollama"),
        ("bedrock", "bedrock"),
        # As the transports name themselves, and the gateway's families.
        ("openai", "openai"),
        ("azure_direct", "azure_direct"),
        ("AWS-Bedrock", "bedrock"),
        ("Mistral", "mistral"),
    ],
)
def test_fa1_every_provider_name_maps_to_the_gateways_family(name: str, family: str) -> None:
    assert provider_family(name) == family


# One request: 1,000 uncached input tokens, 4,000 cached reads, 300 cache writes.
READ, WRITTEN, WHOLE_PROMPT = 4_000, 300, 5_300


@pytest.mark.parametrize(
    ("provider", "transport", "reported_input", "reporting"),
    [
        # OpenAI's input_tokens is the whole prompt: cached reads and cache writes are inside it (SEC1 LC-2).
        ("openAI", "direct", 5_300, "all_in_input"),
        ("azureOpenAI", "direct", 5_300, "all_in_input"),
        ("azure_direct", None, 5_300, "all_in_input"),
        ("gemini", "direct", 5_300, "all_in_input"),
        ("anthropic", "direct", 1_000, "separate"),
        ("bedrock", None, 1_000, "separate"),
        ("ollama", None, 1_000, "separate"),
        # LangChain's usage_metadata sums every input token, for every provider behind it.
        ("azureOpenAI", "langchain", 5_300, "all_in_input"),
        ("anthropic", "langchain", 5_300, "all_in_input"),
        ("langchain", None, 5_300, "all_in_input"),
    ],
)
def test_fa2_the_same_request_as_each_reporter_journaled_it_is_one_prompt(
    provider: str, transport: str | None, reported_input: int, reporting: str
) -> None:
    assert usage_reporting(provider, transport) == reporting
    assert legacy_input_tokens(reported_input, READ, WRITTEN, provider=provider, transport=transport) == WHOLE_PROMPT
