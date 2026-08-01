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
`utils/citations.py::normalize_citations_and_chunks`), emitting the terminal
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
blocks`/`_inject_attachment_blocks`). Attachment handling is now in
``hooks/attachment_resolver.py``: ``resolve_attachments_for_goal``
reads the already-uploaded record from blob and populates citation maps
on the first turn, and ``attachment_rehydration`` (PRE_TURN hook)
re-populates citation maps on follow-up turns.
``shape_image_injection`` (PRE_MODEL hook) injects ``ImagePart`` objects
into the initial ``UserMessage`` when the LLM supports vision.

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
from app.modules.agents.qna.helpers import _tool_names_and_results_from_state
from app.utils.citations import normalize_citations_and_chunks
from app.utils.streaming import parse_confidence_from_answer

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.hooks.citations import CitationCollector
    from app.modules.agents.event_sink import EventSink

logger = logging.getLogger(__name__)

_EMPTY_ANSWER_FALLBACK = "I wasn't able to generate a response. Please try rephrasing."


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
        agent_confidence: str | None = None,
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
        `agent_confidence` is the normalized confidence level from
        `AgentResult.confidence` (populated by `final_answer` when the tool is
        enabled). Takes precedence over the legacy text-trailer parser.
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
                    agent_confidence=agent_confidence,
                )
            except Exception as exc:
                log.error("AnswerFinalizer failed: %s", exc, exc_info=True)
                record_named_span_output(span, {"error": str(exc)})
                return await self._emit_error_response(
                    "I encountered an issue. Please try again.", event_sink=event_sink
                )
            record_named_span_output(span, result)
            return result

    def _normalize_all_parts_citations(
        self,
        parts: list[dict[str, Any]],
        final_results: list[dict[str, Any]],
        records: list[dict[str, Any]],
        ref_to_url: dict[str, str] | None,
        virtual_record_id_to_result: dict[str, dict[str, Any]],
        web_records: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]]]:
        """Normalize citations across ALL text parts for consistent numbering.

        When the model produces multiple text turns (e.g. analysis → tool call
        → summary), citation refs like ``[source](ref2)`` can appear in any
        text part.  Normalizing only the final part would miss citations in
        earlier parts; normalizing each part independently would produce
        inconsistent numbering.  This method combines all text parts, runs
        one ``normalize_citations_and_chunks`` pass, then distributes back,
        returning the final part's text and the unified citation list.
        """
        text_entries: list[tuple[int, str]] = []
        for i, part in enumerate(parts):
            if part.get("type") == "text" and part.get("content"):
                text_entries.append((i, part["content"]))

        if not text_entries:
            return "", []

        if len(text_entries) == 1:
            idx, content = text_entries[0]
            normalized, citations = normalize_citations_and_chunks(
                content, final_results, records,
                ref_to_url=ref_to_url,
                virtual_record_id_to_result=virtual_record_id_to_result,
                web_records=web_records,
            )
            parts[idx]["content"] = normalized
            return normalized, citations

        delimiter = "\n\n\u00a7\u00a7PART_BOUNDARY\u00a7\u00a7\n\n"
        combined = delimiter.join(content for _, content in text_entries)

        normalized_combined, citations = normalize_citations_and_chunks(
            combined, final_results, records,
            ref_to_url=ref_to_url,
            virtual_record_id_to_result=virtual_record_id_to_result,
            web_records=web_records,
        )

        segments = normalized_combined.split(delimiter)
        all_segments: list[str] = []
        for seg_idx, (part_idx, _) in enumerate(text_entries):
            if seg_idx < len(segments):
                parts[part_idx]["content"] = segments[seg_idx]
                all_segments.append(segments[seg_idx])

        return "\n\n".join(all_segments), citations

    async def _run_success_path(
        self,
        state: dict[str, Any],
        log: logging.Logger,
        event_sink: EventSink,
        agent_output: str,
        streamed_answer: str,
        reasoning_turns: list[dict[str, Any]],
        agent_confidence: str | None = None,
    ) -> dict[str, Any]:
        if not agent_output or not agent_output.strip():
            log.warning("AnswerFinalizer: empty response, using fallback")
            answer_text = _EMPTY_ANSWER_FALLBACK
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
                raw_length=len(answer_text),
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
        ref_mapper = self._collector.citation_ref_mapper
        ref_to_url = ref_mapper.ref_to_url if ref_mapper is not None else None
        prior_web_records = self._collector.web_records

        clean_output, trailer_confidence = parse_confidence_from_answer(agent_output)
        # `agent_confidence` (from final_answer.confidence, normalized via the
        # Confidence enum → display label) takes precedence over the legacy
        # text-trailer parser when both are present.
        confidence = agent_confidence if agent_confidence is not None else trailer_confidence

        # Populate parts with confidence-stripped (not yet citation-normalized)
        # text so ALL text parts still have their raw `[source](refN)` refs
        # available for the unified normalization pass below.
        completion_data: dict[str, Any] = {}
        self._attach_parts(completion_data, final_text=clean_output)

        parts = completion_data.get("parts")
        if parts:
            from app.utils.streaming import (  # noqa: PLC0415
                strip_llm_authored_markers_in_parts,
            )

            strip_llm_authored_markers_in_parts(parts)
            normalized, citations = self._normalize_all_parts_citations(
                parts,
                final_results,
                self._collector.tool_records,
                ref_to_url,
                virtual_record_map,
                prior_web_records,
            )
            # _normalize_all_parts_citations returns ALL text parts joined —
            # use only the isFinal part's content as the answer; narration
            # parts are rendered separately in the activity timeline.
            for part in reversed(parts):
                if part.get("isFinal") and part.get("type") == "text":
                    normalized = part["content"]
                    break
        else:
            normalized, citations = normalize_citations_and_chunks(
                clean_output, final_results, self._collector.tool_records,
                ref_to_url=ref_to_url,
                virtual_record_id_to_result=virtual_record_map,
                web_records=prior_web_records,
            )

        if self._context.conversation_id:
            from app.utils.conversation_tasks import await_and_collect_results  # noqa: PLC0415
            from app.utils.streaming import _append_task_markers  # noqa: PLC0415

            task_results = await await_and_collect_results(self._context.conversation_id)
            # Called even with nothing to deliver: it also strips the markers
            # a model writes itself, and a run with no task results is exactly
            # when a hand-written one would otherwise survive to the frontend.
            normalized = _append_task_markers(normalized, task_results)
            if parts:
                # Only the `isFinal` part may carry markers — it is the one
                # `AnswerContent` renders (and the timeline hides). Writing
                # them into any other text part puts them on the timeline's
                # raw-markdown surface instead of into a download card.
                for part in reversed(parts):
                    if part.get("isFinal") and part.get("type") == "text":
                        part["content"] = normalized
                        break

        # A model whose whole answer is the confidence trailer it was told to
        # append clears the raw check above, then strips to nothing here — and
        # Node rejects an empty answer outright (`buildAIResponseMessage`),
        # failing the conversation instead of finishing the run.
        if not normalized.strip():
            log.warning("AnswerFinalizer: answer empty after normalization, using fallback")
            normalized = _EMPTY_ANSWER_FALLBACK
            citations = []
            confidence = "Low"
            completion_data["answerMatchType"] = "Fallback Response"
            self._attach_parts(completion_data, final_text=normalized)

        # `TerminalAnswerStreamer` already streamed citations progressively,
        # but the finalized text differs (confidence stripped, task markers
        # appended). Always emit one authoritative answer_chunk so the
        # streaming state is fully corrected before `complete` fires —
        # the frontend deduplicates citation-map updates by JSON key, so
        # when citations haven't changed this is effectively a no-op.
        # `raw_length` must cover the raw (pre-strip) final-turn text so the
        # AG-UI frontend can adopt this shorter confidence-stripped
        # `normalizedAnswer` — without it, `rawLength: 0` would block adoption
        # and leave `---\nConfidence: High` on screen (see agui-event-handler).
        for evt in self._context.formatter.answer_delta(
            self._context,
            chunk=normalized if streamed_answer.strip() != agent_output.strip() else "",
            accumulated=normalized, citations=citations, confidence=confidence,
            raw_length=len(agent_output),
        ):
            await event_sink.write(evt)

        completion_data["answer"] = normalized
        completion_data["citations"] = citations
        completion_data["confidence"] = confidence
        reasoning_payload = build_reasoning_payload(reasoning_turns)
        if reasoning_payload is not None:
            completion_data["reasoning"] = reasoning_payload
        completion_data.update(_tool_names_from_state(state))
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
            raw_length=len(user_message),
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
