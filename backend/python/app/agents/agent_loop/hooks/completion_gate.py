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

# Two-tier detection: bare file-format keywords (pdf, csv, xlsx, …) are
# ambiguous — they can refer to an UPLOADED input file ("analyze this pdf")
# just as easily as a REQUESTED output ("create a pdf report"). To avoid
# false positives when a user attaches a file for analysis, bare format
# keywords only trigger when paired with a generation-intent verb/phrase.
# Unambiguous output words (spreadsheet, presentation, …) trigger
# unconditionally. Over-triggering burns weak models' turn budgets on
# unnecessary nudges; false negatives are preferred (see module docstring).
#
# "chart"/"graph"/"plot" are deliberately NOT in this unconditional list:
# unlike "spreadsheet" or "presentation", "graph" is an ordinary noun in
# tech/data contexts ("knowledge graph", "graph database", "graph
# traversal") with nothing to do with a requested visualization. They are
# handled below in `_CONTEXTUAL_GENERATION_RE` instead, gated on a
# generation verb, so "implement a knowledge graph system" no longer
# false-positives the way a bare `\bgraph\b` word match did.

_UNAMBIGUOUS_GENERATION_RE = re.compile(
    r"\b("
    r"spreadsheet|presentation|slide\s?deck|"
    r"downloadable\s+file|generate[sd]?\s+a\s+file|"
    r"word\s+document|excel\s+file"
    r")\b",
    re.IGNORECASE,
)

_FORMAT_KW = r"(?:pdf|docx?|xlsx?|pptx?|csv)"
# Visualization nouns — ambiguous on their own (see comment above), so
# they only ever appear inside `_CONTEXTUAL_GENERATION_RE`'s verb-gated
# alternatives below, never in the unconditional regex.
_CHART_KW = r"(?:chart|graph|plot)"
# Creation verbs (a fresh file) + update verbs (an EXISTING one — "on
# existing artifact update" turns rarely say "create"/"generate" again,
# they say "update"/"regenerate"/"revise"/"refresh" the pdf/report).
# `\bgenerate\b` alone does NOT match inside "regenerate" (no word
# boundary before "generate"), so "regenerate" is listed explicitly rather
# than relying on the substring. "fix"/"redo" are deliberately excluded
# here (too generic/overloaded — "fix the bug") and handled below in a
# narrower pattern that requires the format keyword immediately adjacent.
_GENERATION_VERBS = (
    r"create|make|generate|regenerate|build|export|produce|save|convert|"
    r"update|regen|refresh|revise|plot|draw|"
    r"give\s+me|send\s+me|need|want"
)
_CONTEXTUAL_GENERATION_RE = re.compile(
    r"(?:"
    # creation/update verb → optional determiner → 0-2 modifier words → format keyword.
    # Word-count limit (not char-count) prevents "create a ticket for the pdf"
    # from matching while still catching "create a detailed pdf report".
    r"(?:" + _GENERATION_VERBS + r")"
    r"\s+(?:(?:a|an|the|me\s+a|me\s+an)\s+)?"
    r"(?:\S+\s+){0,2}\b" + _FORMAT_KW + r"\b"
    r"|"
    # creation/update verb + dotted file extension (e.g. "export the results.csv")
    # The dot is a strong disambiguator — nobody mentions "results.csv"
    # without referring to an actual file.
    r"(?:" + _GENERATION_VERBS + r")\s.{0,30}\." + _FORMAT_KW + r"\b"
    r"|"
    # "fix"/"redo" only paired with an explicit file-format noun phrase
    # ("fix the pdf", "fix the pdf table") — bare "fix" is too generic
    # (bug reports, typos) to trigger without a format keyword right next
    # to it, unlike the broader verb list above which tolerates 0-2
    # modifier words in between.
    r"\b(?:fix|redo)\s+(?:(?:a|an|the)\s+)?\b" + _FORMAT_KW + r"\b"
    r"|"
    # "as/into/to (a) format"
    r"\b(?:as|into|to)\s+(?:a\s+)?" + _FORMAT_KW + r"\b"
    r"|"
    # "format report/output" — strong signal the format IS the deliverable
    r"\b" + _FORMAT_KW + r"\s+(?:report|output)\b"
    r"|"
    # Same verb-gated shape as the format-keyword alternative above, but
    # for the visualization nouns (chart/graph/plot) pulled out of the
    # unconditional regex — matches "create a bar graph" / "plot a chart
    # of sales", but NOT a bare "graph"/"plot" with no generation verb
    # ("knowledge graph") or a bare verb with no visualization noun
    # ("plot the next chapter").
    r"(?:" + _GENERATION_VERBS + r")"
    r"\s+(?:(?:a|an|the|me\s+a|me\s+an)\s+)?"
    r"(?:\S+\s+){0,2}\b" + _CHART_KW + r"\b"
    r"|"
    # "as/into/to (a) chart/graph/plot" — e.g. "turn this into a chart"
    r"\b(?:as|into|to)\s+(?:a\s+)?" + _CHART_KW + r"\b"
    r")",
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
    positive (spurious nudges on an unrelated request).

    Bare format keywords (pdf, csv, …) require generation-intent context
    to avoid triggering on "analyze this pdf" when the user uploaded a file
    for analysis."""
    for text in texts:
        if not text:
            continue
        if _UNAMBIGUOUS_GENERATION_RE.search(text):
            return True
        if _CONTEXTUAL_GENERATION_RE.search(text):
            return True
    return False


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
