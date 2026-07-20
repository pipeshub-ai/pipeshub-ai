"""POST_MODEL "completion gate": stops a weak model from ending the run
with a text-only answer when the request actually needed a generated file
(PDF, spreadsheet, chart, ...), or with an empty response.

The system prompt already tells the model file generation is MANDATORY via
`run_code`/`coding_agent` (see `prompt_builder.py`'s "Code Execution"
section) — but nothing enforced it: `Agent.step()`'s no-tool-call branch
used to treat ANY response with zero tool calls as a successful, terminal
turn (see `agent/__init__.py`), so a smaller model could "finish" a
"create a PDF" request by describing the PDF in markdown and never once
calling a code-execution tool. This middleware uses the same
`recovery_message` mechanism `truncation_recovery.py` already established
for POST_MODEL: set it, and `Agent.step()` injects it and `continue`s
instead of succeeding.

Deliberately scoped to agents that actually have a code-execution tool
(`run_code`/`coding_agent`) in their own `spec.tool_names` — this hook
fires for every agent in the whole spawn tree sharing one `HookRegistry`
kernel (top-level PipesHub agent AND any composed domain-agent child, e.g.
`calculator_agent`), and nudging an agent with no such tool to "call
run_code" would just waste its (much smaller) turn budget.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.agent_loop_lib.core.messages import AssistantMessage, UserMessage
from app.agent_loop_lib.hooks.middleware.context import ModelResponseContext

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.pipeline import Next
    from app.agents.agent_loop.context import AgentContext

__all__ = ["completion_gate", "looks_like_file_generation_request"]

_DEFAULT_MAX_NUDGES = 2

_FILE_GENERATION_TOOL_NAMES = frozenset({"run_code", "coding_agent"})

# Deliberately narrow: matches the exact set of outputs the system prompt's
# "Code Execution (MANDATORY for file generation)" section calls out (see
# `prompt_builder.py`). Broader wording ("report", "summary", ...) is left
# out on purpose — those are routinely satisfied by a plain text answer,
# and over-triggering here just burns a weak model's turn budget on
# unnecessary nudges.
_FILE_GENERATION_RE = re.compile(
    r"\b("
    r"pdf|docx?|xlsx?|pptx?|csv|"
    r"spreadsheet|presentation|slide\s?deck|"
    r"downloadable\s+file|generate[sd]?\s+a\s+file|"
    r"chart|graph|plot|"
    r"word\s+document|excel\s+file|"
    r"\.pdf|\.docx?|\.xlsx?|\.pptx?|\.csv"
    r")\b",
    re.IGNORECASE,
)

_EMPTY_RESPONSE_NUDGE = (
    "[System: your previous response had no text and called no tool. "
    "Either call a tool to make progress, or provide your final answer as "
    "text now.]"
)

_MISSING_ARTIFACT_NUDGE = (
    "[System: this request requires producing a downloadable file, but you "
    "have not produced one yet. Do not describe the file in text — call "
    "`run_code` (or delegate to `coding_agent`) now to actually generate "
    "it. If you have already tried and it is genuinely not possible, "
    "explain why in your final answer instead of repeating the attempt.]"
)


def looks_like_file_generation_request(*texts: str) -> bool:
    """Deterministic (regex, no LLM call) check over the raw query and/or
    resolved goal description: does this request ask for a generated file?
    Cheap and conservative by design — see the module docstring for why a
    false negative (missed file request) is preferred over a false
    positive (spurious nudges on an unrelated request)."""
    return any(_FILE_GENERATION_RE.search(text) for text in texts if text)


def _response_text(message: object) -> str:
    if isinstance(message, AssistantMessage):
        return message.text
    return ""


def completion_gate(context: "AgentContext", *, max_nudges: int = _DEFAULT_MAX_NUDGES):
    """POST_MODEL middleware factory. `context` is the SAME `AgentContext`
    threaded through the whole request (top-level agent + every spawned
    domain-agent child), so `artifacts_produced_this_run`/
    `completion_gate_nudges` are tracked tree-wide, not per-agent."""

    async def _middleware(ctx: ModelResponseContext, next_fn: "Next") -> None:
        await next_fn()

        if ctx.tool_calls or getattr(ctx.response, "truncated", False):
            return

        text = _response_text(ctx.response)
        run_scope = ctx.scope.run if ctx.scope is not None else None
        tool_names = set(run_scope.spec.tool_names) if run_scope is not None else set()
        can_generate_files = bool(tool_names & _FILE_GENERATION_TOOL_NAMES)

        if not text.strip():
            nudge_text = _EMPTY_RESPONSE_NUDGE
        elif (
            can_generate_files
            and context.file_generation_requested
            and not context.artifacts_produced_this_run
        ):
            nudge_text = _MISSING_ARTIFACT_NUDGE
        else:
            return

        if context.completion_gate_nudges >= max_nudges:
            return
        context.completion_gate_nudges += 1
        ctx.recovery_message = UserMessage(content=nudge_text, injected=True)

    return _middleware
