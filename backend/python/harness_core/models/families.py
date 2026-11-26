"""Provider families: one table for every provider name the harness meets (P4.3).

A provider reaches the harness under several names: as an org configures it
(``azureOpenAI``), as a transport calls itself (``azure_direct``), as the model
gateway groups it.

``TokenUsage.input_tokens`` counts the whole prompt, cached reads and cache
writes inside it, and each transport makes its provider's payload say so. Usage
journaled before that rule kept the reporter's own count: the OpenAI-family
transports, Gemini and LangChain (for every provider) already counted the whole
prompt, Anthropic and Bedrock counted reads and writes apart.
``legacy_input_tokens`` reads such a record, and only the fold calls it.
"""

from __future__ import annotations

from typing import Literal

UsageReporting = Literal["separate", "all_in_input"]

# Lower-cased, ``-``/``_`` removed -> the gateway's family.
_FAMILIES: dict[str, str] = {
    "openai": "openai",
    "openaicompatible": "openai",
    "azureopenai": "azure_direct",
    "azuredirect": "azure_direct",
    "azure": "azure_direct",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "gemini": "gemini",
    "google": "gemini",
    # PipesHub's ``vertexAI`` models are Gemini on Vertex (Anthropic-on-Vertex reports as ``anthropic``).
    "vertexai": "gemini",
    "ollama": "ollama",
    "bedrock": "bedrock",
    "awsbedrock": "bedrock",
}

_PROMPT_IN_INPUT = frozenset({"openai", "azure_direct", "gemini"})
# Transports whose usage is their framework's, whatever the provider behind them.
_ALL_IN_INPUT_TRANSPORTS = frozenset({"langchain"})


def _key(name: str) -> str:
    return name.lower().replace("_", "").replace("-", "")


def provider_family(name: str) -> str:
    """The gateway's family for ``name``; an unknown name is its own family, lower-cased."""
    return _FAMILIES.get(_key(name), name.lower())


def usage_reporting(provider: str, transport: str | None = None) -> UsageReporting:
    """How the reporter of a response counted its input tokens before every transport counted the whole prompt."""
    if transport is not None and _key(transport) in _ALL_IN_INPUT_TRANSPORTS:
        return "all_in_input"
    if _key(provider) in _ALL_IN_INPUT_TRANSPORTS:
        return "all_in_input"
    return "all_in_input" if provider_family(provider) in _PROMPT_IN_INPUT else "separate"


def legacy_input_tokens(
    input_tokens: int, cache_read_tokens: int, cache_write_tokens: int, *, provider: str, transport: str | None
) -> int:
    """The whole prompt of a usage recorded with its reporter's own count (no ``inputIncludesCached``)."""
    if usage_reporting(provider, transport) == "all_in_input":
        return input_tokens
    return input_tokens + cache_read_tokens + cache_write_tokens


__all__ = ["UsageReporting", "legacy_input_tokens", "provider_family", "usage_reporting"]
