from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agent_loop_lib.core.types import Artifact, Confidence, ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.tools.base import ParameterType, Tag, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.tags import TAG_LIFECYCLE_TERMINAL


@dataclass
class TaskCompletionOutcome:
    """Parsed shape of a successful `task_complete` tool result: whether
    the run should actually stop, what to return as the final output, and
    any structured artifacts to attach.

    Lives here (next to the schema/tool that defines this payload's shape)
    rather than in `agent/tool_loop.py`, which just wraps this into its own
    `ToolCallOutcome` — keeping this module free of any dependency on
    `agent/`, and `agent/tool_loop.py`'s import of this module a one-way
    street (no cycle).

    `confidence`/`record_ids`/`needs_input` are the optional typed
    sub-agent output contract (see `AgentResult`, `core/types.py`) —
    always additive to `final_output`, never a replacement for it.
    """

    task_done: bool
    final_output: Any = None
    artifacts: list[Artifact] = field(default_factory=list)
    confidence: Confidence | None = None
    record_ids: list[str] = field(default_factory=list)
    needs_input: str | None = None
    # Non-None means: reject this task_complete call outright (e.g. empty
    # output with no fallback text) — the caller should return this as the
    # tool's result instead of completing the run.
    error_result: CoreToolResult | None = None


class TaskCompleteTool(Tool):
    """Signals the agent loop to stop and return AgentResult."""

    @property
    def name(self) -> str:
        return "task_complete"

    @property
    def short_description(self) -> str:
        return "Signal that the current task is complete and return the final output."

    @property
    def description(self) -> str:
        return "Signal that the current task is complete and return the final output"

    @property
    def path(self) -> str:
        return "/toolsets/builtin/task_complete"

    @property
    def tags(self) -> list[Tag]:
        return [TAG_LIFECYCLE_TERMINAL]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="output",
                type=ParameterType.STRING,
                description="The final output or result of the task",
                required=False,
                default="",
            ),
            ToolParameter(
                name="success",
                type=ParameterType.STRING,
                description="Whether the task succeeded ('true'/'false')",
                required=False,
                default=None,
            ),
            ToolParameter(
                name="artifacts",
                type=ParameterType.ARRAY,
                description=(
                    "Optional structured outputs to attach to the result, e.g. "
                    '[{"name": "report.md", "type": "text", "content": "..."}]. '
                    "type is one of text/json/file/image; content holds the "
                    "value directly, uri points at it instead for large/binary "
                    "outputs."
                ),
                required=False,
                default=None,
                items={"type": "object"},
            ),
            ToolParameter(
                name="confidence",
                type=ParameterType.STRING,
                description=(
                    "Optional: your own confidence that `output` fully and correctly "
                    "answers the goal. Leave unset if unsure — do not guess."
                ),
                required=False,
                default=None,
                enum=["low", "medium", "high"],
            ),
            ToolParameter(
                name="record_ids",
                type=ParameterType.ARRAY,
                description=(
                    "Optional: machine-readable identifiers referenced in `output` "
                    "(ticket keys, document IDs, row IDs) — lets a caller or a "
                    "dependent task cross-reference them without re-parsing your prose."
                ),
                required=False,
                default=None,
                items={"type": "string"},
            ),
            ToolParameter(
                name="needs_input",
                type=ParameterType.STRING,
                description=(
                    "Optional: set this ONLY if you could not fully complete the goal "
                    "because required information is missing and ONLY the human user "
                    "can supply it (you have no ask_user_question tool — this is your "
                    "way to escalate that upward). State exactly what is missing. Still "
                    "provide your best-effort `output` alongside this — never leave it empty."
                ),
                required=False,
                default=None,
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolOutput:
        return ToolOutput(
            success=True,
            data={
                "output": kwargs.get("output", ""),
                "success": kwargs.get("success", "true"),
                "artifacts": kwargs.get("artifacts", []),
                "confidence": kwargs.get("confidence"),
                "record_ids": kwargs.get("record_ids", []),
                "needs_input": kwargs.get("needs_input"),
            },
        )

    @staticmethod
    def extract_outcome(tr: CoreToolResult, call: ToolCall, fallback_text: str) -> TaskCompletionOutcome:
        """Post-process a successful `task_complete` tool result: pulls
        `output`/`artifacts` out of its content, falling back to the
        turn's own response text when the model calls `task_complete()`
        with no `output` argument (some LLMs write the full answer as
        response text and forget to pass it). Assumes `tr.is_error` is
        already False — callers should only reach here on a successful
        result.
        """
        c = tr.content
        output_from_args = c.get("output", "") if isinstance(c, dict) else str(c)
        final_output = output_from_args or fallback_text or ""
        if not str(final_output).strip():
            # Neither an output argument nor response text — completing
            # now would return nothing to the caller. Refuse and make the
            # model call again with the actual result instead of silently
            # "succeeding" with an empty report.
            return TaskCompletionOutcome(
                task_done=False,
                error_result=CoreToolResult(
                    tool_call_id=call.id, name=call.name,
                    content=(
                        "task_complete was called with an empty `output` and there was "
                        "no response text to fall back on — nothing would be returned. "
                        "Call task_complete again with the full final output."
                    ),
                    is_error=True,
                ),
            )

        artifacts: list[Artifact] = []
        raw_artifacts = c.get("artifacts", []) if isinstance(c, dict) else []
        for raw in raw_artifacts or []:
            try:
                artifacts.append(raw if isinstance(raw, Artifact) else Artifact(**raw))
            except Exception:
                # Malformed artifact from the model — drop it rather than
                # failing the whole task_complete call over a formatting slip.
                continue

        confidence: Confidence | None = None
        raw_confidence = c.get("confidence") if isinstance(c, dict) else None
        if raw_confidence:
            try:
                confidence = Confidence(str(raw_confidence).lower())
            except ValueError:
                # An out-of-enum value from the model — same "drop, don't
                # fail the call" treatment as a malformed artifact.
                confidence = None

        record_ids: list[str] = []
        raw_record_ids = c.get("record_ids") if isinstance(c, dict) else None
        if isinstance(raw_record_ids, list):
            record_ids = [str(r) for r in raw_record_ids if str(r).strip()]

        needs_input = c.get("needs_input") if isinstance(c, dict) else None
        needs_input = str(needs_input).strip() or None if needs_input else None

        return TaskCompletionOutcome(
            task_done=True, final_output=final_output, artifacts=artifacts,
            confidence=confidence, record_ids=record_ids, needs_input=needs_input,
        )
