"""`AnswerFinalizer`: deterministic, no-LLM post-processing of the agent's
own final-turn answer for the agent-loop path (originally Phase 6 of the
migration, as `RespondPipeline`; shrunk in the "no separate responder"
fix below).

Originally (`RespondPipeline`, pre-migration) this ran a SECOND LLM call
after `agent.run()` returned, rebuilding a separate text-only conversation
from `tool_state["all_tool_results"]` (`create_response_messages`/
`build_response_prompt`) so a fresh model call could produce the
citation-aware JSON answer. That lost the ReAct loop's own multi-turn
tool-calling context and its own reasoning about the tool results it had
just gathered, and paid for a whole extra model round-trip to re-derive an
answer the agent had, in effect, already written.

Fix #1 (see the "RespondPipeline separate conversation" item in the Opik
tracing/agent-loop-fixes plan — Scoped Option A): `agent.run()`'s ReAct loop
produces the user-facing answer directly — its terminal turn's plain text
(`AgentResult.output`) — using the SAME tool-calling conversation and the
SAME citation-formatting instructions (`prompt_builder.py`'s
`_CITATION_RULES`) this module used to reproduce from scratch.

Fix #2 (live streaming): that terminal turn's text is now streamed to the
client AS IT GENERATES by `answer_streamer.py::TerminalAnswerStreamer`,
consuming `Agent.stream(goal)`'s real per-token events — see
`stream_bridge.py`. This class no longer streams anything itself; its only
remaining job is the deterministic, non-LLM part every path still needs
once the run is over: normalizing `[source](refN)`/URL markers in the
already-produced text into structured `citations` (via
`utils/streaming.py::finalize_agent_answer`), emitting the terminal
`complete` event, and the error/empty-answer/`ask_user_question` fallback
shapes. `streamed_answer` (what `TerminalAnswerStreamer` actually put on
screen) is compared against `AgentResult.output` so the one edge case where
they can diverge — nothing streamed for the terminal turn, or a "degraded"
max_turns answer pulled from an earlier turn (see
`agent_loop_lib/agent/loops.py::_finish_after_max_turns`) — still reaches
the client, as a single full-text `answer_chunk` fallback instead of a
second per-token replay.

Trade-off accepted with this design: no more structured
`answerMatchType`/`referenceData` JSON contract on the success path (the
frontend response shape changes accordingly — see the plan), and current-turn
attachments are no longer resolved into multimodal blocks here (they used to
be injected right before the old second LLM call via `_ensure_attachment_
blocks`/`_inject_attachment_blocks`). The ReAct loop's own tool-calling turns
never saw the current turn's attachments either, so this is not a regression
introduced by removing the second call — it's a pre-existing gap (attachments
were only ever visible during the synthesis call), now more visible. Wiring
current-turn attachments into the ReAct loop's own first turn (agent-loop's
`UserMessage` already supports multimodal `Part` lists — see
`agent_loop_lib/core/messages.py` and `agents/agent_loop/converters.py`) is
tracked as a follow-up, not part of this fix.

Deliberately NOT ported: `respond_node`'s `execution_plan.can_answer_directly`
direct-answer fast path, its `reflection_decision == "respond_clarify"`
branch, and its sub-agent-analysis fast path. All three are driven by state
fields only the LangGraph planner/reflect nodes ever populate
(`execution_plan`, `reflection`, `sub_agent_analyses`) — agent-loop's ReAct
loop has no planner or reflection node, so those fields are never set on
this path and the branches would be dead code here.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.transport.opik_tracing import (
    is_opik_configured,
    maybe_start_named_span,
    record_named_span_output,
)
from app.agents.agent_loop.error_classification import classify_error
from app.agents.agent_loop.hooks.ask_user_question import _ASK_USER_QUESTION_TOOL_NAMES
from app.agents.agent_loop.reasoning_persistence import build_reasoning_payload, filter_reasoning_parts
from app.modules.agents.qna.nodes import (
    _extract_web_records_from_tool_results,
    _tool_names_and_results_from_state,
)
from app.utils.streaming import finalize_agent_answer

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.hooks.citations import CitationCollector
    from app.modules.agents.event_sink import EventSink

logger = logging.getLogger(__name__)


def _tool_names_from_state(state: dict[str, Any]) -> dict[str, Any]:
    """`_tool_names_and_results_from_state` minus the full `tool_results`
    dump — the agent-loop path's own `completion_data` now carries the SAME
    tool activity as bounded `tool_call` parts (see `TranscriptCollector`),
    so resending untruncated external tool payloads over the wire (and,
    from there, into Mongo — nothing downstream persists this key today,
    but shipping it at all defeats "don't store full external tool
    results") is no longer needed. `succeeded_tool_names`/
    `failed_tool_names` are kept — cheap, and still useful without the
    full payload."""
    data = _tool_names_and_results_from_state(state)
    data.pop("tool_results", None)
    return data


class AnswerFinalizer:
    """Normalizes citations on the agent's own final-turn answer, fills in
    the one live-streaming gap (see module docstring), and emits the
    terminal `complete` event. One instance per request, constructed
    BEFORE `agent.run()`/`agent.stream()` starts (its `CitationCollector`
    is a live view `TerminalAnswerStreamer` reads from during the run too
    — see `stream_bridge.py`)."""

    def __init__(self, context: AgentContext, collector: CitationCollector) -> None:
        self._context = context
        self._collector = collector

    def _attach_parts(self, completion_data: dict[str, Any], *, final_text: str | None = None) -> None:
        """Fills `completion_data["parts"]` from `context.transcript_
        collector` — a no-op for `protocol == "legacy"` (`transcript_
        collector` is `None` there), keeping this additive. `final_text`,
        when given, replaces the collector's last streamed `text` part
        with the citation-normalized/fallback/error text actually being
        sent, so the persisted transcript's final segment always matches
        `completion_data["answer"]` (see `TranscriptCollector.
        replace_final_text`)."""
        collector = self._context.transcript_collector
        if collector is None:
            return
        if final_text is not None:
            collector.replace_final_text(final_text)
        completion_data["parts"] = filter_reasoning_parts(collector.parts)

    async def run(
        self,
        *,
        agent_success: bool,
        agent_error: str | None,
        event_sink: EventSink,
        agent_output: Any = None,
        streamed_answer: str = "",
        reasoning_turns: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Produce `completion_data` from the completed agent run.

        `agent_success`/`agent_error`/`agent_output` come straight off
        `AgentResult.success`/`.error`/`.output` — an agent-loop run failure
        (e.g. hit `max_turns`, transport error) maps to the same
        error-response shape `respond_node` produces for `state.get("error")`.
        `streamed_answer` is `TerminalAnswerStreamer.streamed_answer` — what,
        if anything, was already shown to the client live during the run.
        `reasoning_turns` is `TerminalAnswerStreamer.reasoning_turns` — see
        `reasoning_persistence.py` for why this only sometimes reaches
        `completion_data`.
        """
        state = self._context.tool_state
        log = self._context.logger or logger

        if not agent_success:
            return await self._emit_error_response(
                agent_error or "An error occurred", event_sink=event_sink
            )

        with maybe_start_named_span(
            enabled=is_opik_configured(),
            name="answer_finalizer.finalize",
            span_input={
                "query": state.get("query", ""),
                "agent_output": "" if agent_output is None else str(agent_output),
                "agent_success": agent_success,
            },
        ) as span:
            try:
                result = await self._run_success_path(
                    state, log, event_sink,
                    "" if agent_output is None else str(agent_output),
                    streamed_answer, reasoning_turns or [],
                )
            except Exception as exc:
                log.error("AnswerFinalizer failed: %s", exc, exc_info=True)
                record_named_span_output(span, {"error": str(exc)})
                return await self._emit_error_response(
                    "I encountered an issue. Please try again.", event_sink=event_sink
                )
            record_named_span_output(span, result)
            return result

    async def _run_success_path(
        self,
        state: dict[str, Any],
        log: logging.Logger,
        event_sink: EventSink,
        agent_output: str,
        streamed_answer: str,
        reasoning_turns: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not agent_output or not agent_output.strip():
            log.warning("AnswerFinalizer: empty response, using fallback")
            answer_text = "I wasn't able to generate a response. Please try rephrasing."
            fallback_response = {
                "answer": answer_text,
                "citations": [],
                "confidence": "Low",
                "answerMatchType": "Fallback Response",
            }
            fallback_response.update(_tool_names_from_state(state))
            self._attach_parts(fallback_response, final_text=answer_text)
            for evt in self._context.formatter.answer_delta(
                self._context, chunk=answer_text, accumulated=answer_text, citations=[],
            ):
                await event_sink.write(evt)
            await self._emit_ask_user_question_fallback(state, event_sink)
            for evt in self._context.formatter.answer_final(self._context, completion_data=fallback_response):
                await event_sink.write(evt)
            state["response"] = answer_text
            state["completion_data"] = fallback_response
            return fallback_response

        final_results = self._collector.final_results
        virtual_record_map = self._collector.virtual_records
        tool_results = state.get("all_tool_results", [])
        org_id = self._context.org_id
        ref_mapper = self._collector.citation_ref_mapper
        ref_to_url = ref_mapper.ref_to_url if ref_mapper is not None else None
        prior_web_records = _extract_web_records_from_tool_results(tool_results, org_id)

        normalized, citations, confidence = await finalize_agent_answer(
            agent_output,
            final_results,
            self._collector.tool_records,
            virtual_record_id_to_result=virtual_record_map,
            ref_to_url=ref_to_url,
            web_records=prior_web_records,
            conversation_id=self._context.conversation_id,
        )

        # `TerminalAnswerStreamer` already streamed citations progressively,
        # but the finalized text differs (confidence stripped, task markers
        # appended). Always emit one authoritative answer_chunk so the
        # streaming state is fully corrected before `complete` fires —
        # the frontend deduplicates citation-map updates by JSON key, so
        # when citations haven't changed this is effectively a no-op.
        for evt in self._context.formatter.answer_delta(
            self._context,
            chunk=normalized if streamed_answer.strip() != agent_output.strip() else "",
            accumulated=normalized, citations=citations, confidence=confidence,
        ):
            await event_sink.write(evt)

        completion_data: dict[str, Any] = {
            "answer": normalized,
            "citations": citations,
            "confidence": confidence,
        }
        reasoning_payload = build_reasoning_payload(reasoning_turns)
        if reasoning_payload is not None:
            completion_data["reasoning"] = reasoning_payload
        completion_data.update(_tool_names_from_state(state))
        self._attach_parts(completion_data, final_text=normalized)
        state["response"] = normalized
        state["completion_data"] = completion_data
        await self._emit_ask_user_question_fallback(state, event_sink)
        for evt in self._context.formatter.answer_final(self._context, completion_data=completion_data):
            await event_sink.write(evt)
        log.info(
            "AnswerFinalizer: finalized response (%d chars, %d citations)",
            len(normalized), len(citations),
        )
        return completion_data

    async def _emit_ask_user_question_fallback(self, state: dict[str, Any], event_sink: EventSink) -> None:
        """Mirrors `nodes.py::_emit_ask_user_question_tool_event` — a safety
        net for when Phase 5's eager `ask_user_question_sse` POST_TOOL_USE
        hook didn't fire (e.g. `has_ui_client` was false during tool
        orchestration but the flag wasn't re-checked here); gated on the
        same `ask_user_question_emitted` flag so it never double-emits."""
        if state.get("ask_user_question_emitted") or not self._context.has_ui_client:
            return
        for row in _tool_names_and_results_from_state(state).get("tool_results") or []:
            if row.get("tool_name") not in _ASK_USER_QUESTION_TOOL_NAMES:
                continue
            raw_result = row.get("result", "")
            try:
                payload = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
            except (json.JSONDecodeError, TypeError):
                payload = raw_result
            for evt in self._context.formatter.ask_user_question(
                self._context, status=row.get("status"), tool_data=payload,
            ):
                await event_sink.write(evt)
            state["ask_user_question_emitted"] = True

    async def _emit_error_response(self, error_msg: str, *, event_sink: EventSink) -> dict[str, Any]:
        error_code, user_message = classify_error(error_msg)
        error_response = {
            "answer": user_message,
            "citations": [],
            "confidence": "Low",
            "answerMatchType": "Error",
            "errorCode": error_code,
        }
        error_response.update(_tool_names_from_state(self._context.tool_state))
        self._attach_parts(error_response, final_text=user_message)
        for evt in self._context.formatter.answer_delta(
            self._context, chunk=user_message, accumulated=user_message, citations=[],
        ):
            await event_sink.write(evt)
        await self._emit_ask_user_question_fallback(self._context.tool_state, event_sink)
        # Graceful error answer, not a transport failure — the run
        # completed WITH an answer (just an apologetic one), so this is
        # `RUN_FINISHED` in AG-UI mode, never `RUN_ERROR` (that's reserved
        # for pre-stream build failures — see `stream_bridge.py`/
        # `agent.py::_toolset_config_error_stream`).
        for evt in self._context.formatter.answer_final(self._context, completion_data=error_response):
            await event_sink.write(evt)
        self._context.tool_state["response"] = user_message
        self._context.tool_state["completion_data"] = error_response
        return error_response


__all__ = ["AnswerFinalizer"]
