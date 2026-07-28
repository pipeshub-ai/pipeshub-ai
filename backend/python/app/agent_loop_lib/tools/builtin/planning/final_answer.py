"""``FinalAnswerTool`` — user-facing terminal tool that delivers the agent's
answer as a structured call instead of plain response text.

Tagged ``TAG_LIFECYCLE_TERMINAL`` so the loop stops the moment it is
successfully called, reusing the existing tag dispatch in ``tool_loop.py``
with no edits to ``Agent.step()`` or ``ReActLoop``.

Response-format and citation rules live in the ``answer_markdown`` parameter
description rather than the always-on system prompt (see ``prompt_builder.py``).
This puts the rules exactly where the model needs them — when it is writing the
final answer — and shrinks the always-on prompt.

Confidence field is only present when ``confidence_enabled()`` returns True
(controlled by ``PIPESHUB_ENABLE_CONFIDENCE``, default True).  Confidence
uses the existing three-level ``Confidence`` enum (low/medium/high) that
``task_complete`` already populates, avoiding a third vocabulary.

This tool is guarded by ``PIPESHUB_ENABLE_FINAL_ANSWER`` (default True).
Keep it behind that flag until the streaming layer (Phase 7) lands, since
without partial-JSON extraction the live answer stream is broken when this
tool is the terminal tool.
"""

from __future__ import annotations

from typing import Any

from app.agent_loop_lib.core.types import Artifact, Confidence, ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.tools.base import ParameterType, Tag, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompletionOutcome
from app.agent_loop_lib.tools.tags import TAG_LIFECYCLE_TERMINAL


def _answer_markdown_description() -> str:
    """Full description for the ``answer_markdown`` parameter, including
    response-format and citation rules that are removed from the always-on
    system prompt when this tool is enabled."""
    return (
        "Your complete answer to the user's request, in GitHub-flavoured Markdown.\n\n"
        "**Formatting rules**\n"
        "- State findings directly. Render dates/times as 'Mon, Jul 27, 3:45 PM PDT' "
        "using the user's time zone.\n"
        "- Render tool output as markdown tables or prose — never raw JSON.\n"
        "- When a tool result item has a real URL (a Jira issue, a Drive file, a Slack "
        "message, …), render it as `[display name or key](url)`. Include every URL that "
        "exists in the data; a Citation ID is NOT a URL — see Citation Rules below.\n"
        "- For people fields, format as 'Name (email)' when an email address is available.\n"
        "- **Multiple items (3+)**: use a markdown table with columns chosen for comparison "
        "and action. Include the most relevant fields; omit internal IDs, avatar URLs, and "
        "empty fields.\n"
        "- **Single item**: present key fields as a clean summary with the item's title as "
        "a heading.\n"
        "- **Empty results**: say so plainly and suggest broadening the search.\n"
        "- **Partial failure**: when one source is unavailable or returns nothing and another "
        "answers the question, present what you have and name which source was unavailable.\n\n"
        "**Citation rules**\n"
        "Each citable block in a tool result is labelled `Citation ID: ref5` (internal "
        "knowledge, attachments) or `url/Citation ID: https://ref271.xyz` (web). Both are "
        "opaque tokens.\n"
        "- Cite as `[source](<that block's Citation ID>)`, copied exactly: 'Revenue grew "
        "29% [source](ref5).'\n"
        "- Use a Citation ID ONLY inside `[source](...)` — it is an opaque system token, "
        "not a URL the user can open. The system replaces `[source](...)` with a numbered "
        "citation carrying the real link.\n"
        "- Cite the block each fact actually came from; use a distinct ID for each distinct "
        "claim.\n"
        "- One ID per link, inline right after the key claim it supports. Omit the citation "
        "when no Citation ID is available for a fact."
    )


def _build_parameters() -> list[ToolParameter]:
    """Build the parameter list, optionally including the confidence field."""
    from app.agents.agent_loop.confidence import confidence_enabled

    params: list[ToolParameter] = [
        ToolParameter(
            name="answer_markdown",
            type=ParameterType.STRING,
            description=_answer_markdown_description(),
            required=True,
        ),
        ToolParameter(
            name="sources_used",
            type=ParameterType.ARRAY,
            description=(
                "Optional: human-readable names of the sources that contributed to this "
                "answer (e.g. 'Confluence', 'Jira', 'Google Drive'). Used for UI display."
            ),
            required=False,
            default=None,
            items={"type": "string"},
        ),
        ToolParameter(
            name="unavailable_sources",
            type=ParameterType.ARRAY,
            description=(
                "Optional: names of sources that were queried but returned no results or "
                "an error. Listing them here lets the UI warn the user about coverage gaps."
            ),
            required=False,
            default=None,
            items={"type": "string"},
        ),
    ]

    if confidence_enabled():
        from app.agents.agent_loop.confidence import CONFIDENCE_LEVELS, rubric_text
        params += [
            ToolParameter(
                name="confidence",
                type=ParameterType.STRING,
                description=(
                    "Your confidence that answer_markdown fully and correctly addresses the "
                    "user's request. Required when the confidence feature is enabled.\n\n"
                    + rubric_text()
                ),
                required=False,
                default=None,
                enum=list(CONFIDENCE_LEVELS),
            ),
            ToolParameter(
                name="confidence_reason",
                type=ParameterType.STRING,
                description=(
                    "One sentence explaining why you assigned that confidence level. "
                    "Required when confidence is provided."
                ),
                required=False,
                default=None,
            ),
        ]

    return params


class FinalAnswerTool(Tool):
    """Delivers the agent's answer as a structured call, stopping the loop.

    Parameters are built lazily so the confidence field is included only when
    ``PIPESHUB_ENABLE_CONFIDENCE`` is set — avoiding parameter-list churn on
    every import.
    """

    @property
    def name(self) -> str:
        return "final_answer"

    @property
    def short_description(self) -> str:
        return "Deliver your final answer to the user and end the run."

    @property
    def description(self) -> str:
        return (
            "Call this tool ONCE when you are ready to deliver your final answer. "
            "Do not call it mid-research — only after you have gathered all needed information. "
            "Provide the complete answer in answer_markdown using GitHub-flavoured Markdown."
        )

    @property
    def path(self) -> str:
        return "/toolsets/builtin/final_answer"

    @property
    def tags(self) -> list[Tag]:
        return [TAG_LIFECYCLE_TERMINAL]

    @property
    def parameters(self) -> list[ToolParameter]:
        return _build_parameters()

    async def execute(self, **kwargs: Any) -> ToolOutput:
        return ToolOutput(
            success=True,
            data={
                "answer_markdown": kwargs.get("answer_markdown", ""),
                "sources_used": kwargs.get("sources_used") or [],
                "unavailable_sources": kwargs.get("unavailable_sources") or [],
                "confidence": kwargs.get("confidence"),
                "confidence_reason": kwargs.get("confidence_reason"),
            },
        )

    @staticmethod
    def extract_outcome(tr: CoreToolResult, call: ToolCall, fallback_text: str) -> TaskCompletionOutcome:
        """Post-process a successful ``final_answer`` result: extract the
        markdown answer and optional confidence, falling back to the turn's
        response text when the model forgot to pass ``answer_markdown``."""
        c = tr.content
        answer = c.get("answer_markdown", "") if isinstance(c, dict) else str(c)
        final_output = answer or fallback_text or ""

        if not str(final_output).strip():
            return TaskCompletionOutcome(
                task_done=False,
                error_result=CoreToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content=(
                        "final_answer was called with an empty answer_markdown and there was "
                        "no response text to fall back on. Call final_answer again with the "
                        "complete answer in answer_markdown."
                    ),
                    is_error=True,
                ),
            )

        confidence: Confidence | None = None
        raw_confidence = c.get("confidence") if isinstance(c, dict) else None
        if raw_confidence:
            from app.agents.agent_loop.confidence import normalize as _normalize_confidence
            normalised = _normalize_confidence(str(raw_confidence))
            if normalised is not None:
                key = normalised.lower().replace(" ", "_")
                try:
                    confidence = Confidence(key)
                except ValueError:
                    confidence = None

        return TaskCompletionOutcome(
            task_done=True,
            final_output=final_output,
            artifacts=[],
            confidence=confidence,
            record_ids=[],
            needs_input=None,
        )


def final_answer_enabled() -> bool:
    """Return True when PIPESHUB_ENABLE_FINAL_ANSWER is explicitly set to 'true'.

    Defaults to **False**.  The final_answer tool wraps the entire answer in
    a JSON tool-call payload, which breaks streaming for small models and
    adds complexity (StreamingJsonStringExtractor, transport-layer
    ToolCallDeltaEvent handling) for marginal benefit.  The text-based
    approach — model writes plain text, confidence parsed from a trailing
    marker — is more reliable across all model tiers.

    Set to 'true' only for frontier-model deployments that benefit from
    fully structured terminal output.
    """
    from app.agents.agent_loop.env_utils import env_bool
    return env_bool("PIPESHUB_ENABLE_FINAL_ANSWER", False)
