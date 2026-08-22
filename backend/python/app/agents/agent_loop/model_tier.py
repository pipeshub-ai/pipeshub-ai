"""Model-profile classification for tier-aware prompt injection.

The tier determines how much scaffolding the system prompt adds:

* ``FRONTIER`` — large, high-capability models (Anthropic Claude 3.5+,
  GPT-4o, Gemini Ultra, ...).  Prompt stays terse; no worked traces.
* ``MID``       — capable mid-size models (Mistral Medium, GPT-4-mini,
  Gemini Flash, ...).  Explicit rubrics; no worked traces.
* ``SMALL``     — local or quantized models (Ollama/LM Studio, small
  context windows ≤32 k).  Rubrics + worked traces; positive-only
  phrasing preferred.

Classification logic: providers ``ollama`` and ``lmstudio`` (from
``LLMProvider``) are always SMALL regardless of context window, because
local inference is virtually always a quantized small model.  For hosted
providers the context window is a reliable proxy: models with ≤32 k
tokens get SMALL; 32 k–128 k get MID; >128 k get FRONTIER.  Reasoning
models are clamped to FRONTIER (they're always large).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# Context-window thresholds (in tokens) for tier boundaries.
_SMALL_MAX_CONTEXT = 32_768    # ≤ this → SMALL
_MID_MAX_CONTEXT   = 131_072   # ≤ this → MID (else FRONTIER)

# Known local-inference providers — always SMALL regardless of reported ctx.
_LOCAL_PROVIDERS = frozenset({"ollama", "lmstudio", "lm_studio"})


class ModelTier(StrEnum):
    FRONTIER = "frontier"
    MID      = "mid"
    SMALL    = "small"


@dataclass(frozen=True)
class ModelProfile:
    """Immutable per-request snapshot of the model's capability profile."""

    tier: ModelTier
    provider: str
    model_name: str
    context_length: int
    is_reasoning: bool

    @classmethod
    def resolve(
        cls,
        *,
        provider: str,
        model_name: str,
        context_length: int | None,
        is_reasoning: bool,
    ) -> "ModelProfile":
        """Classify the model into a tier from etcd config values.

        ``provider`` is the raw etcd value (e.g. ``"ollama"``,
        ``"openAI"``, ``"anthropic"``).  ``context_length`` is
        ``None`` when admin hasn't filled it in; we fall back to a
        conservative 8 k window so unknown models get the SMALL treatment.
        """
        provider_lower = provider.lower()
        ctx = context_length if context_length and context_length > 0 else 8_192

        if is_reasoning:
            tier = ModelTier.FRONTIER
        elif provider_lower in _LOCAL_PROVIDERS:
            tier = ModelTier.SMALL
        elif ctx <= _SMALL_MAX_CONTEXT:
            tier = ModelTier.SMALL
        elif ctx <= _MID_MAX_CONTEXT:
            tier = ModelTier.MID
        else:
            tier = ModelTier.FRONTIER

        return cls(
            tier=tier,
            provider=provider,
            model_name=model_name,
            context_length=ctx,
            is_reasoning=is_reasoning,
        )

    @property
    def is_small(self) -> bool:
        return self.tier == ModelTier.SMALL

    @property
    def is_frontier(self) -> bool:
        return self.tier == ModelTier.FRONTIER

    def inject_traces(self) -> bool:
        """True for tiers that benefit from worked example traces.

        SMALL models are excluded: they confuse the example format with an
        instruction and emit ``TOOL tool_name | param=...`` as literal text
        instead of making actual function calls (see gemma4 regression).
        """
        return self.tier == ModelTier.MID

    def inject_expanded_rubrics(self) -> bool:
        """True for tiers that benefit from explicit, detailed rubrics."""
        return self.tier in (ModelTier.SMALL, ModelTier.MID)


# Sentinel for requests where no model profile is available (tests, CLI).
UNKNOWN_PROFILE = ModelProfile(
    tier=ModelTier.MID,
    provider="",
    model_name="",
    context_length=8_192,
    is_reasoning=False,
)

__all__ = ["ModelTier", "ModelProfile", "UNKNOWN_PROFILE"]
