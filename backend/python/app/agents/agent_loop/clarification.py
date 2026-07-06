"""Pre-run clarification: when `intent.parse_intent_and_route()` decides a
request is too ambiguous to safely reorganize into a `Goal`
(`IntentRouteDecision.clarifying_questions` non-empty), `stream_bridge.py`
skips `Agent.run()`/`RespondPipeline` entirely and calls
`emit_pre_run_clarification()` instead — ending the turn in one round-trip,
the same way the legacy LangGraph path's `respond_clarify` branch does
(`app/modules/agents/qna/nodes.py`, `reflection_decision == "respond_clarify"`).

Reuses `InternalTools.ask_user_question()` (`app.agents.actions.
internal_tools.intrim_tools`) to build the `toolData` payload instead of
re-deriving the uuid/option-id normalization it already does — the only
reason this doesn't just go through `ToolExecutor.call_tool()` like a real
tool call is that no `Agent`/`ToolRegistry` exists yet at this point in the
request; the questions come straight from the intent LLM call instead of a
tool-calling turn.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agents.actions.internal_tools.intrim_tools import AskUserQuestionItemInput
    from app.agents.agent_loop.context import AgentContext
    from app.modules.agents.event_sink import EventSink

logger = logging.getLogger(__name__)


async def emit_pre_run_clarification(
    context: "AgentContext",
    user_intent: str,
    questions: list["AskUserQuestionItemInput"],
    *,
    event_sink: "EventSink",
) -> dict[str, Any]:
    """Emits the same `ask_user_question` SSE event the main agent's
    `internaltools.ask_user_question` tool produces mid-run
    (`hooks/ask_user_question.py`), followed by `answer_chunk`/`complete`
    events shaped like `RespondPipeline`/`_emit_error_response` produce —
    so the frontend and downstream conversation-history storage see an
    ordinary completed turn, just one that asked a question instead of
    answering.

    Returns the `completion_data` dict, matching `RespondPipeline.run()`'s
    return contract so `stream_bridge.py` can treat both paths uniformly.
    """
    from app.agents.actions.internal_tools.intrim_tools import InternalTools

    tool_data = json.loads(InternalTools().ask_user_question(user_intent=user_intent, questions=questions))

    if context.has_ui_client:
        await event_sink.write({
            "event": "ask_user_question",
            "data": {"status": "success", "toolData": tool_data},
        })

    clarifying_text = questions[0].question if questions else user_intent
    completion_data: dict[str, Any] = {
        "answer": clarifying_text,
        "citations": [],
        "confidence": "Medium",
        "answerMatchType": "Clarification Needed",
    }
    await event_sink.write({
        "event": "answer_chunk",
        "data": {"chunk": clarifying_text, "accumulated": clarifying_text, "citations": []},
    })
    await event_sink.write({"event": "complete", "data": completion_data})

    context.tool_state["response"] = clarifying_text
    context.tool_state["completion_data"] = completion_data
    context.tool_state["ask_user_question_emitted"] = True
    logger.info(
        "Pre-run clarification: asked %d question(s) instead of running the agent (org_id=%s conversation_id=%s)",
        len(questions), context.org_id, context.conversation_id,
    )
    return completion_data


__all__ = ["emit_pre_run_clarification"]
