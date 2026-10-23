"""The PRE_MODEL context-shaping pipeline: one ordered definition.

Every caller (``PipesHubAgentFactory`` for chat, ``ControlPlane`` for the
library and CLI) builds its layers here, so both run the same layers in the
same order with the same protection, and the tests exercise exactly what
production runs. A ``ContextProfile`` says what differs between callers.

Order, least lossy first:

    images     the caller's image injectors (copy-on-write, current request)
    L1  budget_reduction       cap each tool result
    L2  artifact_compaction    old artifacts -> references
    L3  tool_result_clearing   old tool results -> references
    L4  loop_compaction        older runs -> one deterministic summary each
        offload                (optional) large results -> a store
    L6  deterministic_compact  extractive shortening
    L7  auto_compact           LLM summary, one instance per tail ratio
    L5  sliding_window         evict what the layers above could not shrink
    L8  synthesis_guard        last-resort clearing and truncation
    L9  context_invariants     pairing, current request, model window

Every layer protects what ``context/protection.py`` protects, with the
profile's ``protected_tools``, and reuses its earlier compactions from the
run's ``context/compaction_memo.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.hooks.middleware.builtin.artifact_compaction import (
    shape_artifact_compaction,
)
from app.agent_loop_lib.hooks.middleware.builtin.auto_compact import (
    Summarizer,
    shape_auto_compact,
)
from app.agent_loop_lib.hooks.middleware.builtin.budget_reduction import (
    DEFAULT_MAX_RESULT_CHARS,
    shape_budget_reduction,
)
from app.agent_loop_lib.hooks.middleware.builtin.context_invariants import (
    shape_context_invariants,
)
from app.agent_loop_lib.hooks.middleware.builtin.deterministic_compact import (
    shape_deterministic_compact,
)
from app.agent_loop_lib.hooks.middleware.builtin.loop_compaction import (
    shape_loop_compaction,
)
from app.agent_loop_lib.hooks.middleware.builtin.offload import shape_offload
from app.agent_loop_lib.hooks.middleware.builtin.sliding_window import (
    shape_sliding_window,
)
from app.agent_loop_lib.hooks.middleware.builtin.synthesis_guard import (
    shape_synthesis_guard,
)
from app.agent_loop_lib.hooks.middleware.builtin.tool_result_clearing import (
    shape_tool_result_clearing,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ModelCallContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware

#: Layers a profile may switch off. The invariant guard always runs.
OPTIONAL_LAYERS = frozenset({
    "budget_reduction", "artifact_compaction", "tool_result_clearing", "loop_compaction",
    "deterministic_compact", "auto_compact", "sliding_window", "synthesis_guard",
})


@dataclass(frozen=True, slots=True)
class ContextProfile:
    """What one caller's context pipeline protects and how hard each layer
    compacts. Defaults are the library's; the chat factory overrides some."""

    #: Tool groups no layer summarises or shortens (``context/protection.py``).
    protected_tools: frozenset[str] = frozenset()
    #: Tool results tool-result clearing (L3) keeps past its turn window,
    #: which the later layers may still compact by budget.
    clearing_kept_tools: frozenset[str] = frozenset()
    #: LLM summariser for auto-compact; without one, auto-compact is not run.
    summarizer: Summarizer | None = None
    #: PRE_MODEL middleware that put images on the current request, run first.
    image_injectors: tuple[Middleware[ModelCallContext], ...] = ()
    max_result_chars: int = DEFAULT_MAX_RESULT_CHARS
    #: Per-tool caps that replace ``max_result_chars`` for that tool.
    tool_caps: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    disabled: frozenset[str] = frozenset()

    artifact_keep_last_n_turns: int = 1
    artifact_trigger_ratio: float = 0.5
    clearing_keep_last_n_turns: int = 3
    clearing_trigger_ratio: float = 0.5
    loop_compact_keep_recent: int = 6
    loop_compact_trigger_ratio: float = 0.6
    #: Offload large results to a store; None leaves the layer out.
    offload_threshold_tokens: int | None = None
    offload_preview_lines: int = 10
    deterministic_compact_trigger_ratio: float = 0.85
    deterministic_compact_keep_last_n: int = 6
    deterministic_compact_preview_chars: int = 100
    auto_compact_trigger_ratio: float = 0.85
    #: One auto-compact instance per ratio, gentlest first.
    auto_compact_tail_ratios: tuple[float, ...] = (0.6,)
    synthesis_guard_keep_last_n: int = 2

    def __post_init__(self) -> None:
        unknown = self.disabled - OPTIONAL_LAYERS
        if unknown:
            raise ValueError(f"unknown context layers: {sorted(unknown)}")


def build_context_pipeline(profile: ContextProfile) -> list[Middleware[ModelCallContext]]:
    """The PRE_MODEL layers for ``profile``, in order (see the module docstring)."""
    on = OPTIONAL_LAYERS - profile.disabled
    protected = profile.protected_tools
    layers: list[Any] = list(profile.image_injectors)
    if "budget_reduction" in on:
        layers.append(shape_budget_reduction(
            max_result_chars=profile.max_result_chars, tool_caps=dict(profile.tool_caps) or None,
        ))
    if "artifact_compaction" in on:
        layers.append(shape_artifact_compaction(
            trigger_ratio=profile.artifact_trigger_ratio,
            keep_last_n_turns=profile.artifact_keep_last_n_turns,
        ))
    if "tool_result_clearing" in on:
        layers.append(shape_tool_result_clearing(
            keep_last_n_turns=profile.clearing_keep_last_n_turns,
            trigger_ratio=profile.clearing_trigger_ratio,
            protected_tools=protected | profile.clearing_kept_tools,
        ))
    if "loop_compaction" in on:
        layers.append(shape_loop_compaction(
            keep_recent=profile.loop_compact_keep_recent,
            trigger_ratio=profile.loop_compact_trigger_ratio,
            protected_tools=protected,
        ))
    if profile.offload_threshold_tokens is not None:
        layers.append(shape_offload(
            threshold_tokens=profile.offload_threshold_tokens, preview_lines=profile.offload_preview_lines,
        ))
    if "deterministic_compact" in on:
        layers.append(shape_deterministic_compact(
            trigger_ratio=profile.deterministic_compact_trigger_ratio,
            keep_last_n_messages=profile.deterministic_compact_keep_last_n,
            preview_chars=profile.deterministic_compact_preview_chars,
            protected_tools=protected,
        ))
    if "auto_compact" in on and profile.summarizer is not None:
        layers.extend(
            shape_auto_compact(
                summarizer=profile.summarizer,
                trigger_ratio=profile.auto_compact_trigger_ratio,
                max_tail_ratio=ratio,
                protected_tools=protected,
            )
            for ratio in profile.auto_compact_tail_ratios
        )
    if "sliding_window" in on:
        layers.append(shape_sliding_window(protected_tools=protected))
    if "synthesis_guard" in on:
        layers.append(shape_synthesis_guard(
            keep_last_n_tool_results=profile.synthesis_guard_keep_last_n, protected_tools=protected,
        ))
    layers.append(shape_context_invariants())
    return layers


__all__ = ["OPTIONAL_LAYERS", "ContextProfile", "build_context_pipeline"]
