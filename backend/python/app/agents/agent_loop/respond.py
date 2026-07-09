"""`RespondPipeline`: post-agent response formatting for the agent-loop path
(Phase 6 of the migration).

Originally this ran a SECOND LLM call after `agent.run()` returned, rebuilding
a separate text-only conversation from `tool_state["all_tool_results"]`
(`create_response_messages`/`build_response_prompt`) so a fresh model call
could produce the citation-aware JSON answer. That lost the ReAct loop's own
multi-turn tool-calling context and its own reasoning about the tool results
it had just gathered, and paid for a whole extra model round-trip to
re-derive an answer the agent had, in effect, already written.

Fix (see the "RespondPipeline separate conversation" item in the Opik
tracing/agent-loop-fixes plan — Scoped Option A): `agent.run()`'s ReAct loop
now produces the user-facing answer directly, via `task_complete(output=...)`
(`AgentResult.output`), using the SAME tool-calling conversation and the SAME
citation-formatting instructions (`prompt_builder.py`'s `_CITATION_RULES`)
this module used to reproduce from scratch. This class's only remaining job
is the deterministic, non-LLM part every path still needs: normalizing
`[source](refN)`/URL markers in that text into structured `citations` against
the accumulated retrieval/web results, chunking the text for streaming, and
draining background artifact tasks. All three are delegated UNCHANGED to
`stream_llm_response_with_tools`'s `mode="simple"` fast path
(`handle_simple_mode`, `utils/streaming.py`, which already special-cases "the
last message is already a finished AI answer" for exactly this shape) —
called with `tools=None` so it does NOT run a second tool-calling round or a
second LLM call, just formats text already in hand.

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
from app.modules.agents.qna.nodes import (
    _extract_web_records_from_tool_results,
    _tool_names_and_results_from_state,
)
from app.utils.streaming import stream_llm_response_with_tools

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.hooks.citations import CitationCollector
    from app.modules.agents.event_sink import EventSink

logger = logging.getLogger(__name__)

_DEFAULT_CONTEXT_LENGTH = 128_000


class RespondPipeline:
    """Formats the agent's own final-turn answer with citations and streams
    it out. One instance per request, constructed after `agent.run()`
    returns. See module docstring for why this is not a second LLM call.
    """

    def __init__(self, context: AgentContext, collector: CitationCollector) -> None:
        self._context = context
        self._collector = collector

    async def run(
        self,
        *,
        agent_success: bool,
        agent_error: str | None,
        event_sink: EventSink,
        agent_output: Any = None,
    ) -> dict[str, Any]:
        """Produce `completion_data` from the completed agent run, streaming
        `answer_chunk`/`status`/`complete` events through `event_sink` as
        `respond_node` does through `safe_stream_write`.

        `agent_success`/`agent_error`/`agent_output` come straight off
        `AgentResult.success`/`.error`/`.output` — an agent-loop run failure
        (e.g. hit `max_turns`, transport error) maps to the same
        error-response shape `respond_node` produces for `state.get("error")`.
        """
        state = self._context.tool_state
        log = self._context.logger or logger

        if not agent_success:
            return await self._emit_error_response(
                agent_error or "An error occurred", event_sink=event_sink
            )

        # No LLM call happens inside `_run_success_path` anymore (see module
        # docstring) — this span still exists because it's the only thing
        # that puts citation-formatting/streaming latency and the final
        # answer in the Opik trace tree for this phase.
        with maybe_start_named_span(
            enabled=is_opik_configured(),
            name="respond_pipeline.synthesis",
            span_input={
                "query": state.get("query", ""),
                "agent_output": "" if agent_output is None else str(agent_output),
                "agent_success": agent_success,
            },
        ) as span:
            try:
                result = await self._run_success_path(
                    state, log, event_sink, "" if agent_output is None else str(agent_output)
                )
            except Exception as exc:
                log.error("RespondPipeline failed: %s", exc, exc_info=True)
                record_named_span_output(span, {"error": str(exc)})
                return await self._emit_error_response(
                    "I encountered an issue. Please try again.", event_sink=event_sink
                )
            record_named_span_output(span, result)
            return result

    async def _run_success_path(
        self, state: dict[str, Any], log: logging.Logger, event_sink: EventSink, agent_output: str,
    ) -> dict[str, Any]:
        await event_sink.write({
            "event": "status",
            "data": {"status": "generating", "message": "Generating response..."},
        })

        from langchain_core.messages import AIMessage as _AIMessage

        final_results = self._collector.final_results
        virtual_record_map = self._collector.virtual_records
        tool_results = state.get("all_tool_results", [])
        org_id = self._context.org_id
        ref_mapper = self._collector.citation_ref_mapper
        prior_web_records = _extract_web_records_from_tool_results(tool_results, org_id)

        answer_text = ""
        citations: list[Any] = []
        confidence = None

        # `tools=None` skips `execute_tool_calls()` entirely — the ReAct loop
        # already ran every tool it needed. `messages=[AIMessage(agent_output)]`
        # hits `handle_simple_mode`'s "already-finished AI answer" fast path,
        # which just normalizes citations and chunks the text; no model call.
        async for stream_event in stream_llm_response_with_tools(
            llm=self._context.llm,
            messages=[_AIMessage(content=agent_output)],
            final_results=final_results,
            all_queries=[],
            retrieval_service=self._context.retrieval_service,
            user_id=self._context.user_id,
            org_id=org_id,
            virtual_record_id_to_result=virtual_record_map,
            blob_store=self._context.blob_store,
            is_multimodal_llm=self._context.is_multimodal_llm,
            context_length=_DEFAULT_CONTEXT_LENGTH,
            tools=None,
            tool_runtime_kwargs=None,
            target_words_per_chunk=1,
            mode="simple",
            conversation_id=self._context.conversation_id,
            is_service_account=self._context.is_service_account,
            ref_mapper=ref_mapper,
            initial_web_records=prior_web_records,
            initial_records=self._collector.tool_records,
        ):
            event_type = stream_event.get("event")
            event_data = stream_event.get("data", {})

            await self._emit_ask_user_question_fallback(state, event_sink)
            await event_sink.write({"event": event_type, "data": event_data})

            if event_type == "complete":
                answer_text = event_data.get("answer", "")
                citations = event_data.get("citations", [])
                confidence = event_data.get("confidence")

        if not answer_text or not answer_text.strip():
            log.warning("RespondPipeline: empty response, using fallback")
            answer_text = "I wasn't able to generate a response. Please try rephrasing."
            fallback_response = {
                "answer": answer_text,
                "citations": [],
                "confidence": "Low",
                "answerMatchType": "Fallback Response",
            }
            fallback_response.update(_tool_names_and_results_from_state(state))
            await event_sink.write({
                "event": "answer_chunk",
                "data": {"chunk": answer_text, "accumulated": answer_text, "citations": []},
            })
            await self._emit_ask_user_question_fallback(state, event_sink)
            await event_sink.write({"event": "complete", "data": fallback_response})
            state["response"] = answer_text
            state["completion_data"] = fallback_response
            return fallback_response

        completion_data: dict[str, Any] = {
            "answer": answer_text,
            "citations": citations,
            "confidence": confidence,
        }
        completion_data.update(_tool_names_and_results_from_state(state))
        state["response"] = answer_text
        state["completion_data"] = completion_data
        log.info(
            "RespondPipeline: generated response (%d chars, %d citations)",
            len(answer_text), len(citations),
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
            await event_sink.write({
                "event": "ask_user_question",
                "data": {"status": row.get("status"), "toolData": payload},
            })
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
        error_response.update(_tool_names_and_results_from_state(self._context.tool_state))
        await event_sink.write({
            "event": "answer_chunk",
            "data": {"chunk": user_message, "accumulated": user_message, "citations": []},
        })
        await self._emit_ask_user_question_fallback(self._context.tool_state, event_sink)
        await event_sink.write({"event": "complete", "data": error_response})
        self._context.tool_state["response"] = user_message
        self._context.tool_state["completion_data"] = error_response
        return error_response


__all__ = ["RespondPipeline"]
