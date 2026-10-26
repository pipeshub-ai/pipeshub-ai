"""Offline lookup against a pruned LiteLLM price catalog.

Keys outside OpenAI and Anthropic are usually ``{provider}/{id}``. A missing
flag is left unset so callers do not treat "not in the file" as "false".
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_CATALOG_PATH = Path(__file__).with_name("data") / "model_catalog.json"

# Our provider ids to the prefix LiteLLM uses in catalog keys.
_LITELLM_PREFIX = {
    "openAI": "openai",
    "azureOpenAI": "azure",
    "azureAI": "azure",
    "anthropic": "anthropic",
    "gemini": "gemini",
    "vertexAI": "vertex_ai",
    "groq": "groq",
    "mistral": "mistral",
    "cohere": "cohere",
    "together": "together_ai",
    "fireworks": "fireworks_ai",
    "openRouter": "openrouter",
    "xai": "xai",
    "bedrock": "bedrock",
    "voyage": "voyage",
    "jinaAI": "jina_ai",
    "minimax": "minimax",
    "ollama": "ollama",
    "lmStudio": "lm_studio",
}

_MODE_TO_CAPABILITY = {
    "chat": "llm",
    "completion": "llm",
    "responses": "llm",
    "embedding": "embedding",
    "image_generation": "imageGeneration",
    "image": "imageGeneration",
    "audio_speech": "tts",
    "audio_transcription": "stt",
}

# Modes we deliberately do not offer as selectable models.
_EXCLUDED_MODES = {"moderation", "rerank", "search"}


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, dict[str, Any]]:
    if not _CATALOG_PATH.is_file():
        return {}
    with _CATALOG_PATH.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        return {}
    return {key: value for key, value in payload.items() if isinstance(value, dict)}


def lookup_catalog(provider_id: str, model_id: str) -> dict[str, Any] | None:
    catalog = load_catalog()
    prefix = _LITELLM_PREFIX.get(provider_id)
    if prefix:
        prefixed = catalog.get(f"{prefix}/{model_id}")
        if prefixed is not None:
            return prefixed
    return catalog.get(model_id)


def capability_from_mode(mode: str | None) -> str | None:
    if not mode or mode in _EXCLUDED_MODES:
        return None
    return _MODE_TO_CAPABILITY.get(mode)
