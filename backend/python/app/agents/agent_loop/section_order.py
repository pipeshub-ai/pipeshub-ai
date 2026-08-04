"""Declared section order and volatility for `PipesHubPromptBuilder`.

This module is the single source of truth for:
1. What named sections the builder produces.
2. Their order (stable-first, operative-content-last within each band).
3. Their volatility band — which determines the Anthropic cache split.

Three bands, ordered stable-first so the cache breakpoint sits just before
the first turn-volatile byte:

  Band A (STATIC)      — invariant for a given agent; never changes.
  Band B (CONV)        — stable within a conversation; grows on `fetch_tools`
                         or when `dynamic_fetch_full_record` is first granted,
                         but is otherwise unchanged across turns.
  Band C (TURN)        — changes per turn (time, follow-up context, attachments,
                         hook-injected extra sections).

`build()` renders all three bands joined by double-newlines.  `build_blocks()`
returns `(stable_block, volatile_block)` where:
  stable_block  = Band A + Band B joined
  volatile_block = Band C joined (may be empty string)

Anthropic puts `cache_control` on `stable_block` only, leaving `volatile_block`
uncached — so the ~85% of the prompt that doesn't change per turn is read from
cache at 10% of fresh-token cost on turns 2..N and on every follow-up message.
"""
from __future__ import annotations

from enum import StrEnum


class Volatility(StrEnum):
    STATIC = "static"
    CONV = "conv"
    TURN = "turn"


#: Ordered (name, volatility) pairs.  `build()` renders sections in this order;
#: `build_blocks()` splits at the first TURN section.
PIPESHUB_SECTION_ORDER: tuple[tuple[str, Volatility], ...] = (
    # ── Band A — static ──────────────────────────────────────────────────────
    ("identity",                   Volatility.STATIC),
    ("agent_instructions",         Volatility.STATIC),
    # Org-level "Custom Instructions" (workspace settings), mode-resolved by
    # `chat_modes.bridge` — Chat Assistant only, never set for Agent Builder.
    ("custom_instructions",        Volatility.STATIC),
    ("goal_brief",                 Volatility.STATIC),
    ("operating_rules",            Volatility.STATIC),
    ("response_format",            Volatility.STATIC),
    ("base_system_prompt",         Volatility.STATIC),
    ("citation_rules",             Volatility.STATIC),
    # The one source-precedence section (replaces web_search_rules,
    # internal_knowledge_first, full_record_policy, hybrid_strategy, and
    # tool_selection_strategy — see `_build_finding_information`).
    ("finding_information",        Volatility.STATIC),
    ("code_execution",             Volatility.STATIC),
    # ── Band B — conversation-stable ─────────────────────────────────────────
    # `available_tools` grows when fetch_tools loads new toolsets; everything
    # before it in Band A still caches even if this block invalidates.
    ("available_tools",            Volatility.CONV),
    ("knowledge_sources",          Volatility.CONV),
    ("capability_summary",         Volatility.CONV),
    ("user_context",               Volatility.CONV),
    ("skills_overview",            Volatility.CONV),
    ("answer_confidence",          Volatility.CONV),
    # Worked examples — injected for SMALL and MID tiers only; omitted for
    # FRONTIER where the function schemas are sufficient scaffolding.
    ("worked_traces",              Volatility.CONV),
    # ── Band C — turn-volatile ────────────────────────────────────────────────
    ("time_context",               Volatility.TURN),
    ("request_context",            Volatility.TURN),
    ("attachments",                Volatility.TURN),
    ("extra_sections",             Volatility.TURN),
)

#: The set of section names whose content changes on every turn.
#: Used by `build_blocks()` to partition stable vs. volatile.
TURN_VOLATILE_NAMES: frozenset[str] = frozenset(
    name for name, v in PIPESHUB_SECTION_ORDER if v == Volatility.TURN
)

#: All declared section names in order — for test assertions.
SECTION_NAMES: tuple[str, ...] = tuple(name for name, _ in PIPESHUB_SECTION_ORDER)
