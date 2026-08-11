"""`resolve_capability(provider, model)` — the model-sensitive lookup the
plan's review notes call out as the single thing a provider-keyed
registry would get wrong: cache mode and minimum prefix length are
properties of the *model*, not just the provider (GPT-5.6+ explicit vs
GPT-5.5 automatic; Gemini 2.5's 2,048-token floor vs 3.x's 4,096; Claude
tiers from 1,024 to 2,048).

Thresholds below are the best publicly documented figures at time of
writing (each provider's prompt-caching docs) and are intentionally
centralized here as the single place to revise them — nothing else in
this package hardcodes a token floor or a write/read multiplier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

CacheMode = Literal["explicit", "automatic", "none"]

# Anthropic-shaped multipliers are the only ones with hard confirmation
# in this codebase today (`budget/pricing.py`); other providers' listed
# discounts are directionally correct (OpenAI/Gemini ~50% cached-read
# discount) but Phase 7 is where `pricing.py` gets a per-provider table
# reconciled against actual billing rather than this module guessing —
# these values exist here only so `CacheCapability` has something
# non-`None` to report per model, not as a billing source of truth.
_ANTHROPIC_READ_MULTIPLIER = 0.10
_ANTHROPIC_WRITE_MULTIPLIER = 1.25
_OPENAI_READ_MULTIPLIER = 0.50
_OPENAI_WRITE_MULTIPLIER = 1.0  # OpenAI does not charge a write premium.
_GOOGLE_READ_MULTIPLIER = 0.25
_GOOGLE_WRITE_MULTIPLIER = 1.0


@dataclass(frozen=True)
class CacheCapability:
    mode: CacheMode
    min_prefix_tokens: int
    """Cumulative prefix floor (chars/4 approximation), not per-block."""
    max_breakpoints: int
    """0 for automatic modes — there is nothing to place."""
    default_ttl: str
    extended_ttl: str | None
    write_multiplier: float
    read_multiplier: float
    can_cache_tools: bool
    can_cache_system: bool


_NONE_CAPABILITY = CacheCapability(
    mode="none",
    min_prefix_tokens=0,
    max_breakpoints=0,
    default_ttl="5m",
    extended_ttl=None,
    write_multiplier=1.0,
    read_multiplier=1.0,
    can_cache_tools=False,
    can_cache_system=False,
)


def _anthropic_capability(min_prefix_tokens: int) -> CacheCapability:
    return CacheCapability(
        mode="explicit",
        min_prefix_tokens=min_prefix_tokens,
        max_breakpoints=4,
        default_ttl="5m",
        extended_ttl="1h",
        write_multiplier=_ANTHROPIC_WRITE_MULTIPLIER,
        read_multiplier=_ANTHROPIC_READ_MULTIPLIER,
        can_cache_tools=True,
        can_cache_system=True,
    )


# Model-name patterns, most specific first — `resolve_capability` walks
# these in order and returns the first match. Haiku tiers have a higher
# token floor than Sonnet/Opus on the Anthropic API.
_ANTHROPIC_RULES: list[tuple[re.Pattern[str], CacheCapability]] = [
    (re.compile(r"claude-.*haiku", re.IGNORECASE), _anthropic_capability(2048)),
    (re.compile(r"claude-(opus|sonnet)", re.IGNORECASE), _anthropic_capability(1024)),
    # claude-2 / claude-1 / claude-instant never supported prompt caching.
    (re.compile(r"claude-(2|1|instant)", re.IGNORECASE), _NONE_CAPABILITY),
]

# GPT-5.6+ adds explicit breakpoints (`prompt_cache_breakpoint`,
# `prompt_cache_key`); everything from gpt-4o onward already gets
# OpenAI's automatic >1024-token caching with zero request changes.
_OPENAI_EXPLICIT_PATTERN = re.compile(r"gpt-(5\.[6-9]|[6-9]|\d{2,})", re.IGNORECASE)
_OPENAI_AUTOMATIC_PATTERN = re.compile(
    r"gpt-(4o|4\.1|5(\.[0-5])?)|^o[1-4](-|$)", re.IGNORECASE
)


def _openai_capability(mode: CacheMode) -> CacheCapability:
    return CacheCapability(
        mode=mode,
        min_prefix_tokens=1024,
        max_breakpoints=4 if mode == "explicit" else 0,
        # "30m" is currently the ONLY value `prompt_cache_options.ttl`
        # accepts (OpenAI/Azure docs) — unlike Anthropic's 5m/1h choice,
        # there is no shorter default to report here.
        default_ttl="30m",
        extended_ttl=None,
        write_multiplier=_OPENAI_WRITE_MULTIPLIER,
        read_multiplier=_OPENAI_READ_MULTIPLIER,
        # `prompt_cache_breakpoint` lands on a MESSAGE content block
        # (text/image_url/input_audio/file) — there is no way to place
        # it on the `tools` array itself, unlike Anthropic's per-tool
        # `cache_control`. Tool schemas still ride along inside
        # whatever message-level prefix precedes/follows them; they
        # just aren't an independent breakpoint target.
        can_cache_tools=False,
        can_cache_system=mode == "explicit",
    )


# Gemini 2.5 has a 2,048-token floor; the 3.x generation raised it to
# 4,096. Implicit (automatic) caching needs no request change on either
# — see the plan's corner case "zero measured hit rate is not always a
# bug": Google may discount without ever reporting `cache_read`.
_GEMINI_3X_PATTERN = re.compile(r"gemini-3", re.IGNORECASE)
_GEMINI_2X_PATTERN = re.compile(r"gemini-2", re.IGNORECASE)


def _gemini_capability(min_prefix_tokens: int) -> CacheCapability:
    return CacheCapability(
        mode="automatic",
        min_prefix_tokens=min_prefix_tokens,
        max_breakpoints=0,
        default_ttl="5m",
        extended_ttl=None,
        write_multiplier=_GOOGLE_WRITE_MULTIPLIER,
        read_multiplier=_GOOGLE_READ_MULTIPLIER,
        can_cache_tools=False,
        can_cache_system=False,
    )


# Phase 9 spike result (source-inspected against the pinned
# langchain-aws==1.1.0 tag, see pyproject.toml): Bedrock's InvokeModel API
# genuinely honors `cache_control` on Claude models (AWS's own docs confirm
# the exact "cache_control": {"type": "ephemeral"} wire format), and
# `ChatBedrock._format_anthropic_messages` DOES forward `cache_control` on
# regular message content blocks. BUT two things block wiring this up on
# PipesHub's actual (invoke-kwarg) path today:
#   1. System-prompt `cache_control` is silently DROPPED in 1.1.0 — system
#      content is flattened to a plain string before caching info survives
#      (langchain-aws#793, fixed in #838). That's the highest-value target
#      (mirrors the agent loop's stable band), so losing it defeats most of
#      the point.
#   2. The `cache_control=...` INVOKE KWARG convenience that
#      `resolve_langchain_cache_kwargs` relies on for `ChatAnthropic` was
#      only ADDED to `ChatBedrock` in langchain-aws 1.4.0 (#838/#839,
#      merged 2026-03-04) — it does not exist at the pinned 1.1.0, so
#      sending it today would be a no-op at best.
# Upgrading langchain-aws to >=1.4.0 (or migrating to `ChatBedrockConverse`,
# which the langchain-aws maintainers explicitly recommend over `ChatBedrock`
# and which has never had this bug) would unblock this — both are
# dependency/migration decisions outside this plan's scope. Resolve to
# `mode="none"` until one of those lands, rather than promising a
# breakpoint shape that's currently either silently ignored or unavailable.
_BEDROCK_CAPABILITY = _NONE_CAPABILITY


def resolve_capability(provider: str, model_name: str | None) -> CacheCapability:
    """Ordered (provider, model-pattern) rules, falling back to a
    conservative `mode="none"` capability for anything unrecognized.

    An unknown model on a KNOWN provider must degrade to no-caching
    rather than guessing a mode the API could reject with a 400 — the
    conservative default at the bottom of this function, not a
    per-provider default capability.
    """
    provider_key = (provider or "").lower()
    model = model_name or ""

    if provider_key == "anthropic":
        for pattern, capability in _ANTHROPIC_RULES:
            if pattern.search(model):
                return capability
        # Unknown Claude model name: assume the more conservative
        # (higher) floor rather than the Sonnet/Opus 1,024 minimum.
        return _anthropic_capability(2048) if model else _NONE_CAPABILITY

    if provider_key in ("openai", "azure_openai"):
        if _OPENAI_EXPLICIT_PATTERN.search(model):
            return _openai_capability("explicit")
        if _OPENAI_AUTOMATIC_PATTERN.search(model) or model:
            return _openai_capability("automatic")
        return _NONE_CAPABILITY

    if provider_key == "google":
        if _GEMINI_3X_PATTERN.search(model):
            return _gemini_capability(4096)
        if _GEMINI_2X_PATTERN.search(model):
            return _gemini_capability(2048)
        return _NONE_CAPABILITY

    if provider_key == "bedrock":
        return _BEDROCK_CAPABILITY

    return _NONE_CAPABILITY


__all__ = ["CacheCapability", "CacheMode", "resolve_capability"]
