from __future__ import annotations

from typing import Any

from app.agent_loop_lib.agent import observability as obs
from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.special_route import RouteContext

"""`request_review` — the probabilistic/interactive half of
`supervisor.supervisor.Supervisor.review()`'s MEDIUM-confidence branch,
exposed as an agent-callable tool (see `hooks/middleware/builtin/supervisor_gate.py`
for the deterministic LOW-confidence half, and `.claude/rules/
principles.md`'s "Confidence routing" section: "Medium -> critique first,
then Supervisor review (HIL or senior agent escalation)").

Escalating to a human or a senior agent is inherently interactive — it
cannot be a hook, since a hook can never itself decide when to ask a human
something; it can only enforce a rule. This tool lets any agent (not just
the root agent's preamble, which is the only caller of `Supervisor.review()`
today) explicitly ask for review mid-loop when its own confidence is
uncertain, escalating via the same HIL/senior-agent paths `Supervisor`
already knows how to use.
"""

__all__ = ["RequestReviewTool"]


class RequestReviewTool(Tool):
    @property
    def name(self) -> str:
        return "request_review"

    @property
    def short_description(self) -> str:
        return "Escalate a medium-confidence plan or decision for human/senior-agent review."

    @property
    def description(self) -> str:
        return (
            "Escalate a plan or decision you're not fully confident in for "
            "review — via a human-in-the-loop approval if configured, otherwise "
            "proceeds automatically. Use this instead of guessing when your own "
            "confidence is medium rather than high."
        )

    @property
    def path(self) -> str:
        return "/toolsets/builtin/request_review"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="question",
                type=ParameterType.STRING,
                description="What you want reviewed/approved, phrased as a yes/no question.",
                required=True,
            ),
        ]

    async def handle(self, call: ToolCall, ctx: RouteContext) -> CoreToolResult:
        from app.agent_loop_lib.modules.stores.hil.base import (
            HILRequest,
            HILRequestType,
        )

        agent = ctx.agent
        runtime = ctx.runtime
        question = call.arguments.get("question", "")

        if runtime.hil_store is None:
            # No escalation configured — proceed, mirroring
            # Supervisor.review()'s fallback for the same case.
            return CoreToolResult(
                tool_call_id=call.id, name=call.name,
                content={"approved": True, "reason": "no HIL store configured — proceeding"},
                is_error=False,
            )

        hil_request = HILRequest(
            request_type=HILRequestType.PLAN_REVIEW,
            run_id=agent.run_ctx.run_id,
            session_id=agent.session_id,
            question=question,
            context={"goal": ctx.goal.description},
        )
        request_id = await runtime.hil_store.submit(hil_request)

        await obs.save_checkpoint(
            agent, "hil_pause", ctx.goal, ctx.messages, ctx.turn_index,
            current_tool="request_review",
            hil_request_id=request_id,
            pending_tool_call_id=call.id,
        )

        hil_response = await runtime.hil_store.wait_for_response(request_id)
        return CoreToolResult(
            tool_call_id=call.id, name=call.name,
            content={"approved": hil_response.approved, "reason": hil_response.answer or ""},
            is_error=False,
        )

    async def execute(self, **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data={"approved": True, "reason": "no HIL store in standalone execute()"})
