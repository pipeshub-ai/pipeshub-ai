"""Known model-endpoint incompatibilities, as data (C13.03).

A row names an endpoint (a host, optionally narrowed to model ids) and what it is known not to accept.
``None`` means "not known": the caller keeps its own rule (the prompt-cache policy in
``transports/prompt_cache.py``, the health check's image probe). ``False`` is a documented "does not
accept", so the caller skips the parameter instead of learning it from a failed call. A row is never a
``True`` that switches a feature on where the caller's own rule says no.

Each row cites the vendor page it was read from, so a reviewer can re-check it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlparse

from harness_core.models.open_models import open_model_pin


@dataclass(frozen=True, slots=True)
class CapabilityRow:
    # A hostname, or ``.suffix`` for every host under it.
    host: str
    # The model ids the row covers; ``None`` covers every model on the host.
    model: re.Pattern[str] | None
    # Prompt-cache parameters: Anthropic ``cache_control`` markers, OpenAI ``prompt_cache_key``.
    prompt_cache: bool | None
    # Image blocks in a user turn.
    image_input: bool | None
    source: str
    read_on: str


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    prompt_cache: bool | None = None
    image_input: bool | None = None


UNKNOWN: Final = ModelCapabilities()

MODEL_CAPABILITY_ROWS: Final[tuple[CapabilityRow, ...]] = (
    # DeepSeek's Anthropic-compatible API ignores ``cache_control`` and does not support image content.
    CapabilityRow(
        host="api.deepseek.com",
        model=None,
        prompt_cache=False,
        image_input=False,
        source="https://api-docs.deepseek.com/guides/anthropic_api",
        read_on="2026-11-02",
    ),
)


def model_capabilities(
    model: str | None, base_url: str | None, rows: tuple[CapabilityRow, ...] = MODEL_CAPABILITY_ROWS,
) -> ModelCapabilities:
    """What the rows know about ``model`` at ``base_url``; ``UNKNOWN`` when no row matches.

    A provider's default endpoint (no base URL) matches no row: the rows describe endpoints an admin
    points a provider at, and a vendor's own API is the caller's default rule."""
    host = _host(base_url)
    if host is None:
        return UNKNOWN
    name = model or ""
    for row in rows:
        if _host_matches(host, row.host) and (row.model is None or row.model.search(name)):
            return ModelCapabilities(prompt_cache=row.prompt_cache, image_input=row.image_input)
    # An open model an admin serves at their own endpoint is known by its pinned row (C13.04), whatever the host.
    pin = open_model_pin(model)
    if pin is not None and not pin.image_input:
        return ModelCapabilities(image_input=False)
    return UNKNOWN


def _host(base_url: str | None) -> str | None:
    if not base_url:
        return None
    parsed = urlparse(base_url if "://" in base_url else f"https://{base_url}")
    return (parsed.hostname or "").lower() or None


def _host_matches(host: str, pattern: str) -> bool:
    if pattern.startswith("."):
        return host.endswith(pattern) or host == pattern[1:]
    return host == pattern
