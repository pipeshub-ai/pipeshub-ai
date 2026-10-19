"""Size and thinking traits of a chat model, used by the Auto model router.

Each field resolves on its own, first hit wins: the value stored on the model
entry, then the family overlay, then what the provider reported, then the
catalog, then name and price rules. For size, a parameter count comes before
the overlay. Size falls back to ``medium``.

These tiers are not ``agents/agent_loop/model_tier.py``'s SMALL/MID/FRONTIER,
which only shape prompt scaffolding and may disagree (it calls every Ollama
model SMALL).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from app.services.ai_models.discovery.catalog import lookup_catalog

if TYPE_CHECKING:
    from collections.abc import Mapping

Size = Literal["small", "medium", "large"]
Thinking = Literal["none", "optional", "always"]

SIZES: tuple[Size, ...] = ("small", "medium", "large")
THINKING_MODES: tuple[Thinking, ...] = ("none", "optional", "always")
# Platform effort levels a router may choose; "none" is floored to "low" by the LLM factory.
EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "max")
DEFAULT_EFFORTS: tuple[str, ...] = ("low", "medium", "high")

# Output price bands in USD per 1M tokens.
PRICE_SMALL_MAX = 1.5
PRICE_LARGE_MIN = 8.0

# Parameter-count bands in billions.
PARAMS_SMALL_MAX = 15.0
PARAMS_LARGE_MIN = 80.0

_OVERLAY_PATH = Path(__file__).with_name("discovery") / "data" / "model_traits.json"

_EFFORT_ALIASES = {"minimal": "low", "xhigh": "max", "none": "low"}

_PARAMS_RE = re.compile(r"(?<![a-z0-9.])(?:(\d+)x)?(\d+(?:\.\d+)?)\s*b(?![a-z])", re.IGNORECASE)
# Active-parameter suffixes ("30b-a3b") describe compute, not capability; ignore them.
_ACTIVE_PARAMS_RE = re.compile(r"-a\d+(?:\.\d+)?b\b", re.IGNORECASE)
_SMALL_WORDS = re.compile(r"(?:^|[-_/:.\s])(mini|nano|lite|flash|haiku|small|tiny)(?:$|[-_/:.\s\d])", re.IGNORECASE)
_LARGE_WORDS = re.compile(r"(?:^|[-_/:.\s])(pro|opus|large|ultra)(?:$|[-_/:.\s\d])", re.IGNORECASE)


class ModelTraits(BaseModel):
    size: Size = "medium"
    thinking: Thinking = "none"
    reasoning_efforts: list[str] = Field(default_factory=list, serialization_alias="reasoningEfforts")
    supports_tools: bool | None = Field(default=None, serialization_alias="supportsTools")
    strengths: list[str] | None = None
    source: dict[str, str] = Field(default_factory=dict)


@lru_cache(maxsize=1)
def load_overlay() -> list[dict[str, Any]]:
    if not _OVERLAY_PATH.is_file():
        return []
    with _OVERLAY_PATH.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    rules = payload.get("rules") if isinstance(payload, dict) else payload
    compiled = []
    for rule in rules or []:
        if not isinstance(rule, dict) or not rule.get("match"):
            continue
        compiled.append({**rule, "_re": re.compile(rule["match"], re.IGNORECASE)})
    return compiled


def _overlay_rule(provider: str, model_name: str) -> dict[str, Any]:
    for rule in load_overlay():
        providers = rule.get("providers")
        if providers and provider not in providers:
            continue
        if rule["_re"].search(model_name):
            return rule
    return {}


def normalize_efforts(values: object) -> list[str]:
    """Map provider effort names onto platform levels, ordered low to max."""
    if not isinstance(values, (list, tuple)):
        return []
    seen = set()
    for value in values:
        level = _EFFORT_ALIASES.get(str(value).strip().lower(), str(value).strip().lower())
        if level in EFFORT_LEVELS:
            seen.add(level)
    return [level for level in EFFORT_LEVELS if level in seen]


def parse_parameter_count(text: str | None) -> float | None:
    """Billions of parameters named in a model id or an Ollama ``parameter_size``."""
    if not text:
        return None
    cleaned = _ACTIVE_PARAMS_RE.sub("", str(text))
    best = None
    for experts, count in _PARAMS_RE.findall(cleaned):
        value = float(count) * (int(experts) if experts else 1)
        best = value if best is None else max(best, value)
    return best


def size_from_parameters(billions: float | None) -> Size | None:
    if billions is None or billions <= 0:
        return None
    if billions < PARAMS_SMALL_MAX:
        return "small"
    if billions > PARAMS_LARGE_MIN:
        return "large"
    return "medium"


def size_from_price(output_price_per_million: float | None) -> Size | None:
    if output_price_per_million is None or output_price_per_million <= 0:
        return None
    if output_price_per_million < PRICE_SMALL_MAX:
        return "small"
    if output_price_per_million >= PRICE_LARGE_MIN:
        return "large"
    return "medium"


def size_from_name(model_name: str) -> Size | None:
    base = model_name.rsplit("/", 1)[-1]
    if _SMALL_WORDS.search(base):
        return "small"
    if _LARGE_WORDS.search(base):
        return "large"
    return None


def catalog_output_price(catalog: Mapping[str, Any] | None) -> float | None:
    if not catalog:
        return None
    per_token = catalog.get("output_cost_per_token")
    if isinstance(per_token, (int, float)) and per_token > 0:
        return float(per_token) * 1_000_000
    return None


def _catalog_efforts(catalog: Mapping[str, Any] | None) -> list[str]:
    if not catalog:
        return []
    levels = []
    for key, value in catalog.items():
        match = re.fullmatch(r"supports_(\w+)_reasoning_effort", key)
        if match and value:
            levels.append(match.group(1))
    efforts = normalize_efforts(levels)
    if efforts:
        # Flags name the extra levels; the standard ones are implied.
        return normalize_efforts([*DEFAULT_EFFORTS, *efforts])
    return []


def _valid(value: object, allowed: tuple[str, ...]) -> str | None:
    text = str(value).strip().lower() if value is not None else ""
    return text if text in allowed else None


def resolve_traits(
    provider: str,
    model_name: str,
    configuration: Mapping[str, Any] | None = None,
    *,
    entry: Mapping[str, Any] | None = None,
    provider_facts: Mapping[str, Any] | None = None,
    single_model_entry: bool = True,
) -> ModelTraits:
    """Resolve size, thinking and effort levels for one model name.

    ``configuration`` is the entry's stored configuration; its traits are
    honoured only for single-model entries, since an older comma-separated
    entry holds several models under one configuration. ``entry`` supplies the
    legacy ``isReasoning`` flag. ``provider_facts`` carries discovery data:
    ``parameter_count``, ``thinking``, ``reasoning_efforts``, ``output_price``,
    ``supports_tools``.
    """
    stored = configuration if (configuration and single_model_entry) else {}
    facts = provider_facts or {}
    rule = _overlay_rule(provider, model_name)
    catalog = lookup_catalog(provider, model_name)
    source: dict[str, str] = {}

    size = _valid(stored.get("size"), SIZES)
    if size:
        source["size"] = "stored"
    else:
        # A parameter count beats family rules: "deepseek-r1:7b" is a small distill, not the large R1.
        for label, candidate in (
            ("parameters", size_from_parameters(facts.get("parameter_count"))),
            ("parameters", size_from_parameters(parse_parameter_count(model_name))),
            ("overlay", _valid(rule.get("size"), SIZES)),
            ("price", size_from_price(facts.get("output_price") or catalog_output_price(catalog))),
            ("name", size_from_name(model_name)),
        ):
            if candidate:
                size, source["size"] = candidate, label
                break
        else:
            size, source["size"] = "medium", "default"

    thinking = _valid(stored.get("thinking"), THINKING_MODES)
    if thinking:
        source["thinking"] = "stored"
    else:
        catalog_reasoning = catalog.get("supports_reasoning") if catalog else None
        legacy = (entry or {}).get("isReasoning")
        for label, candidate in (
            ("overlay", _valid(rule.get("thinking"), THINKING_MODES)),
            ("provider", _valid(facts.get("thinking"), THINKING_MODES)),
            ("catalog", None if catalog_reasoning is None else ("optional" if catalog_reasoning else "none")),
            ("legacy", "optional" if legacy else None),
        ):
            if candidate:
                thinking, source["thinking"] = candidate, label
                break
        else:
            thinking, source["thinking"] = "none", "default"

    efforts: list[str] = []
    if thinking != "none":
        for label, candidate in (
            ("stored", normalize_efforts(stored.get("reasoningEfforts"))),
            ("overlay", normalize_efforts(rule.get("reasoningEfforts"))),
            ("provider", normalize_efforts(facts.get("reasoning_efforts"))),
            ("catalog", _catalog_efforts(catalog)),
        ):
            if candidate:
                efforts, source["reasoningEfforts"] = candidate, label
                break
        else:
            efforts, source["reasoningEfforts"] = list(DEFAULT_EFFORTS), "default"

    supports_tools = stored.get("supportsTools")
    if not isinstance(supports_tools, bool):
        supports_tools = facts.get("supports_tools")
    if not isinstance(supports_tools, bool) and catalog and "supports_function_calling" in catalog:
        supports_tools = bool(catalog["supports_function_calling"])
    if not isinstance(supports_tools, bool):
        supports_tools = None

    strengths = stored.get("strengths")
    if not isinstance(strengths, list):
        strengths = None

    return ModelTraits(
        size=size,
        thinking=thinking,
        reasoning_efforts=efforts,
        supports_tools=supports_tools,
        strengths=strengths,
        source=source,
    )
