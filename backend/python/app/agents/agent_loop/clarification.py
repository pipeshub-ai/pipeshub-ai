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

Mid-run ask-user go/no-go (Multi-Agent Pipeline Fixes plan, Phase 2b):
NO-GO on `agent_loop_lib`'s blocking `HILStore.wait_for_response()` /
`clarify` checkpoint machinery for PipesHub's web path. That mechanism
(`agent/observability.py::handle_clarify`, wired up in `cli.py`/
`control_plane.py`) suspends the CURRENT process/task while awaiting an
answer — workable for a long-lived CLI/control-plane process, but a poor
fit for one stateless request-per-turn web worker (`runtime.hil_store` is
never set here; `PipesHubAgentFactory` doesn't register the library's
`clarify` tool at all). PipesHub's actual mid-run mechanism —
`ask_user_question` tagged `TAG_LIFECYCLE_TERMINAL` — deliberately ends
the turn instead of blocking, and the user's answer arrives as an
ordinary NEW turn; "resume" is therefore conversation-history
continuity, not in-process suspension. Phase 2a (`tool_results` ->
`previousConversations`, see `factory.py::_convert_conversation_turn`)
is what actually closes that continuity gap, and already ships without
this. Revisit only if PipesHub moves to a long-lived worker-per-run
model; if the sibling `agent_loop_architecture_fixes` plan's ASK->HIL
item lands a real persistent `HILStore` first, prefer reusing it over
building a second one.
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

    tool_data = json.loads(await InternalTools().ask_user_question(user_intent=user_intent, questions=questions))

    if context.has_ui_client:
        for evt in context.formatter.ask_user_question(context, status="success", tool_data=tool_data):
            await event_sink.write(evt)

    clarifying_text = questions[0].question if questions else user_intent
    completion_data: dict[str, Any] = {
        "answer": clarifying_text,
        "citations": [],
        "confidence": "Medium",
        "answerMatchType": "Clarification Needed",
    }
    for evt in context.formatter.answer_delta(
        context, chunk=clarifying_text, accumulated=clarifying_text, citations=[],
    ):
        await event_sink.write(evt)
    for evt in context.formatter.answer_final(context, completion_data=completion_data):
        await event_sink.write(evt)

    context.tool_state["response"] = clarifying_text
    context.tool_state["completion_data"] = completion_data
    context.tool_state["ask_user_question_emitted"] = True
    logger.info(
        "Pre-run clarification: asked %d question(s) instead of running the agent (org_id=%s conversation_id=%s)",
        len(questions), context.org_id, context.conversation_id,
    )
    return completion_data


__all__ = ["emit_pre_run_clarification"]
