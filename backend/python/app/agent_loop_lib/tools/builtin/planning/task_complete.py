from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agent_loop_lib.core.types import Artifact, ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter


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
    """

    task_done: bool
    final_output: Any = None
    artifacts: list[Artifact] = field(default_factory=list)
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
        ]

    async def execute(self, **kwargs: Any) -> ToolOutput:
        return ToolOutput(
            success=True,
            data={
                "output": kwargs.get("output", ""),
                "success": kwargs.get("success", "true"),
                "artifacts": kwargs.get("artifacts", []),
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

        return TaskCompletionOutcome(task_done=True, final_output=final_output, artifacts=artifacts)
