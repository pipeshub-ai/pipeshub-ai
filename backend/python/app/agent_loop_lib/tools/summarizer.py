"""`ToolSummarizer`: the narrow abstraction `agent/tool_loop.py` depends on
to turn a tool call's raw arguments/result into a short, human-readable
description before it ever reaches an `EventEmitter`.

`agent_loop_lib` is the generic library layer and must never import from
`app/agents/agent_loop` (the PipesHub integration layer) — the concrete,
per-tool-aware implementation (`PipesHubToolSummarizer`) lives there and is
injected onto `AgentRuntime.summarizer` by `factory.py`, exactly the same
DIP shape `AgentRuntime.spec_factory` already uses to resolve role names
without depending on the concrete role registry/factory module.

Two failure modes this module deliberately protects against:
1. A missing summarizer (`runtime.summarizer is None`, e.g. `ControlPlane`
   standalone agents that never wire one) — every field on `ToolCallSummary`
   is optional so "no summary" is just the empty default, never an error.
2. A buggy per-tool formatter — `ToolSummarizer` implementations are
   expected to catch their own exceptions and degrade to an empty
   `ToolCallSummary` (see `PipesHubToolSummarizer`); this protocol makes no
   promises about that, callers must not assume summarization can fail.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel

if TYPE_CHECKING:
    from app.agent_loop_lib.core.types import ToolResult

__all__ = ["ArgsFormatter", "ResultFormatter", "ToolCallSummary", "ToolSummarizer"]

# Shared formatter signatures for both a tool's own `@tool(args_summary=...,
# result_summary=...)` declaration (see `tools/decorators.py`) and any
# name-keyed fallback registry (see `app/agents/agent_loop/tool_summarizer.
# py`'s `PipesHubToolSummarizer`) — one shape, two call sites, so a
# formatter function written for one is a drop-in for the other.
ArgsFormatter = Callable[[dict[str, Any]], "str | None"]
ResultFormatter = Callable[[dict[str, Any], "ToolResult"], "str | None"]


class ToolCallSummary(BaseModel):
    """Human-readable stand-ins for a tool call's raw arguments/result —
    additive alongside the existing truncated `args`/`content` preview
    fields already flowing through `AgentEvent` payloads, never a
    replacement for them (both `AGUIEventEmitter` and `TranscriptCollector`
    keep forwarding the raw preview so older frontends/persisted messages
    are unaffected)."""

    args_summary: str | None = None
    result_summary: str | None = None


class ToolSummarizer(Protocol):
    """Implemented by `app.agents.agent_loop.tool_summarizer.
    PipesHubToolSummarizer`. `tool_loop.py` only ever calls through this
    protocol, never the concrete class — keeps the dependency arrow
    pointing from PipesHub's integration layer into the generic library,
    not the reverse."""

    def summarize_args(self, tool_name: str, args: dict[str, Any]) -> str | None:
        """A short description of what the call is about to do, computed
        from the (small, LLM-authored) arguments alone — always cheap,
        never needs the failure-isolation `summarize_result` requires."""
        ...

    def summarize_result(
        self, tool_name: str, args: dict[str, Any], result: "ToolResult"
    ) -> ToolCallSummary:
        """A short description of both the args and the outcome, computed
        from the FULL `result.content`/`result.sources` — must be called
        before any truncation is applied to the result being described."""
        ...
