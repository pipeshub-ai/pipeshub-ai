"""Candidate models for one Auto turn, their hard filters, and the tier policy."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from app.services.ai_models.discovery.catalog import lookup_catalog
from app.services.ai_models.traits import (
    EFFORT_LEVELS,
    SIZES,
    ModelTraits,
    catalog_output_price,
    resolve_traits,
)
from app.utils.aimodels import iter_model_names

Need = Literal["light", "standard", "heavy"]
NEEDS: tuple[Need, ...] = ("light", "standard", "heavy")
_NEED_SIZE: dict[str, str] = {"light": "small", "standard": "medium", "heavy": "large"}

# Routers behind one model id would hide the real model and stack two routing decisions.
_HOSTED_ROUTER = re.compile(r"(^|/)auto$|model-router|jev-router|(^|/)router$", re.IGNORECASE)

# Rough characters-per-token for the prompt estimate; retrieval adds more, so keep headroom.
_CHARS_PER_TOKEN = 4
_PROMPT_HEADROOM_TOKENS = 16_000


@dataclass
class Candidate:
    entry: dict[str, Any]
    model_name: str
    traits: ModelTraits
    order: int

    @property
    def model_key(self) -> str:
        return str(self.entry.get("modelKey") or "")

    @property
    def is_default(self) -> bool:
        return bool(self.entry.get("isDefault"))

    @property
    def is_reasoning(self) -> bool:
        return bool(self.entry.get("isReasoning"))

    @property
    def size_rank(self) -> int:
        return SIZES.index(self.traits.size)

    @property
    def output_price(self) -> float:
        price = catalog_output_price(lookup_catalog(str(self.entry.get("provider") or ""), self.model_name))
        return price if price is not None else float("inf")

    def identity(self) -> tuple[str, str]:
        return self.model_key, self.model_name


def _parse_agent_models(agent_models: list[Any]) -> list[tuple[str, str | None]]:
    """The agent's ``models`` list as (modelKey, modelName) pairs; same shapes ``agent.py`` accepts."""
    pairs: list[tuple[str, str | None]] = []
    for item in agent_models or []:
        if isinstance(item, str) and "_" in item:
            key, name = item.split("_", 1)
            pairs.append((key, name or None))
        elif isinstance(item, str) and item:
            pairs.append((item, None))
        elif isinstance(item, dict) and item.get("modelKey"):
            pairs.append((str(item["modelKey"]), item.get("modelName")))
    return pairs


def build_pool(llm_configs: list[dict[str, Any]], agent_models: list[Any] | None = None) -> list[Candidate]:
    """Every configured (entry, model name), narrowed to the agent's models when it lists any."""
    allowed = _parse_agent_models(agent_models) if agent_models else None
    pool: list[Candidate] = []
    for entry in llm_configs or []:
        if not isinstance(entry, dict):
            continue
        configuration = entry.get("configuration") if isinstance(entry.get("configuration"), dict) else {}
        names = iter_model_names(configuration)
        for name in names:
            if allowed is not None and not any(
                key == entry.get("modelKey") and (allowed_name is None or allowed_name == name)
                for key, allowed_name in allowed
            ):
                continue
            traits = resolve_traits(
                str(entry.get("provider") or ""), name, configuration,
                entry=entry, single_model_entry=len(names) == 1,
            )
            pool.append(Candidate(entry=entry, model_name=name, traits=traits, order=len(pool)))
    return pool


def estimate_prompt_tokens(query: str, previous_conversations: list[dict[str, Any]] | None) -> int:
    chars = len(query or "")
    for turn in previous_conversations or []:
        if isinstance(turn, dict):
            chars += len(str(turn.get("content") or ""))
    return chars // _CHARS_PER_TOKEN + _PROMPT_HEADROOM_TOKENS


def apply_filters(
    pool: list[Candidate], *, needs_vision: bool, needs_tools: bool, prompt_tokens: int,
) -> list[Candidate]:
    kept = []
    for candidate in pool:
        if _HOSTED_ROUTER.search(candidate.model_name):
            continue
        if needs_vision and not candidate.entry.get("isMultimodal"):
            continue
        if needs_tools and candidate.traits.supports_tools is False:
            continue
        context = candidate.entry.get("contextLength")
        if isinstance(context, int) and 0 < context < prompt_tokens:
            continue
        kept.append(candidate)
    return kept


def _preference(candidate: Candidate) -> tuple:
    return (0 if candidate.is_default else 1, candidate.output_price, candidate.order)


def pick_for_need(pool: list[Candidate], need: Need, pinned: Candidate | None) -> Candidate:
    """Smallest size at or above the need; the largest when none is; the pin when it still fits.

    A conversation never moves to a smaller model than the pinned one, which
    would also throw away the provider's prompt cache.
    """
    wanted = SIZES.index(_NEED_SIZE[need])
    if pinned is not None and pinned.size_rank >= wanted:
        return pinned
    floor = max(wanted, pinned.size_rank if pinned is not None else 0)
    fitting = [c for c in pool if c.size_rank >= floor]
    if fitting:
        smallest = min(c.size_rank for c in fitting)
        return min((c for c in fitting if c.size_rank == smallest), key=_preference)
    largest = max(c.size_rank for c in pool)
    return min((c for c in pool if c.size_rank == largest), key=_preference)


def choose_effort(
    candidate: Candidate,
    need: Need,
    *,
    needs_reasoning: bool = False,
    floor: str | None = None,
    explicit: str | None = None,
) -> str | None:
    """Effort for the chosen model, in platform levels; ``None`` when it takes no effort setting.

    The LLM factory applies effort only to entries flagged ``isReasoning``, and
    falls back to "high" when given ``None``, so reasoning entries always get
    an explicit level here.
    """
    if not candidate.is_reasoning:
        return None
    supported = candidate.traits.reasoning_efforts or ["low", "medium", "high"]
    if explicit in EFFORT_LEVELS:
        level = explicit
    else:
        level = "high" if (need == "heavy" or needs_reasoning) else ("medium" if need == "standard" else "low")
        if floor in EFFORT_LEVELS and EFFORT_LEVELS.index(floor) > EFFORT_LEVELS.index(level):
            level = floor
        if level == "max":
            level = "high"
    return _clamp(level, supported)


def _clamp(level: str, supported: list[str]) -> str:
    ordered = [lvl for lvl in EFFORT_LEVELS if lvl in supported] or ["low", "medium", "high"]
    if level in ordered:
        return level
    rank = EFFORT_LEVELS.index(level)
    lower = [lvl for lvl in ordered if EFFORT_LEVELS.index(lvl) < rank]
    return lower[-1] if lower else ordered[0]
