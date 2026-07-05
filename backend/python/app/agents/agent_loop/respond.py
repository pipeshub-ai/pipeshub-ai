"""`RespondPipeline`: post-agent response synthesis for the agent-loop path
(Phase 6 of the migration).

agent-loop's `Agent.run()` only does tool orchestration — it returns an
`AgentResult` once the model stops calling tools (or emits `task_complete`).
PipesHub's response synthesis (citation-aware JSON answer, streamed word by
word, with background-artifact draining) is a SEPARATE phase today
(`nodes.py::respond_node`) that runs after the LangGraph planner/execute/
reflect loop finishes. `RespondPipeline` is that same phase, unplugged from
LangGraph: it runs once, after `agent.run()` returns, reading the same
`AgentContext.tool_state` dict Phase 5's hooks already populated during tool
orchestration (`all_tool_results`, `final_results`,
`virtual_record_id_to_result`, `citation_ref_mapper`) instead of a `ChatState`
threaded through graph nodes.

Everything content-bearing is reused UNCHANGED from the existing modules —
`create_response_messages` / `build_response_prompt` (`response_prompt.py`),
`stream_llm_response_with_tools` (`streaming.py`, which itself drains
background artifact tasks via `conversation_tasks.await_and_collect_results`
when given a `conversation_id` — no separate draining step needed here),
`_build_tool_results_context` (`context/tool_results_context.py`),
`CitationRefMapper` / `get_message_content` (`chat_helpers.py`),
`normalize_citations_and_chunks_for_agent` / `normalize_reference_data_items`
(citation normalization). This module only adapts the CALLING convention:
`EventSink.write()` instead of `safe_stream_write(writer, ...)`, and reads
from `AgentContext`/`CitationCollector` instead of `ChatState` fields sourced
from LangGraph node inputs.

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

from app.agents.agent_loop.error_classification import classify_error
from app.agents.agent_loop.hooks.ask_user_question import _ASK_USER_QUESTION_TOOL_NAMES
from app.modules.agents.qna.nodes import (
    _ensure_attachment_blocks,
    _extract_web_records_from_tool_results,
    _inject_attachment_blocks,
    _tool_names_and_results_from_state,
)
from app.modules.agents.qna.reference_data import normalize_reference_data_items
from app.modules.qna.response_prompt import create_response_messages
from app.utils.chat_helpers import CitationRefMapper
from app.utils.citations import normalize_citations_and_chunks_for_agent
from app.utils.streaming import stream_llm_response_with_tools

if TYPE_CHECKING:
    from langchain_core.messages import HumanMessage

    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.hooks.citations import CitationCollector
    from app.modules.agents.event_sink import EventSink

logger = logging.getLogger(__name__)

_DEFAULT_CONTEXT_LENGTH = 128_000


class RespondPipeline:
    """Post-agent response synthesis with citations and streaming.

    One instance per request, constructed after `agent.run()` returns.
    """

    def __init__(self, context: AgentContext, collector: CitationCollector) -> None:
        self._context = context
        self._collector = collector

    async def run(self, *, agent_success: bool, agent_error: str | None, event_sink: EventSink) -> dict[str, Any]:
        """Produce `completion_data` from the completed agent run, streaming
        `answer_chunk`/`status`/`complete` events through `event_sink` as
        `respond_node` does through `safe_stream_write`.

        `agent_success`/`agent_error` come from `AgentResult.success`/`.error`
        — an agent-loop run failure (e.g. hit `max_turns`, transport error)
        maps to the same error-response shape `respond_node` produces for
        `state.get("error")`.
        """
        state = self._context.tool_state
        log = self._context.logger or logger

        if not agent_success:
            return await self._emit_error_response(
                agent_error or "An error occurred", event_sink=event_sink
            )

        try:
            return await self._run_success_path(state, log, event_sink)
        except Exception as exc:
            log.error("RespondPipeline failed: %s", exc, exc_info=True)
            return await self._emit_error_response(
                "I encountered an issue. Please try again.", event_sink=event_sink
            )

    async def _run_success_path(
        self, state: dict[str, Any], log: logging.Logger, event_sink: EventSink
    ) -> dict[str, Any]:
        await event_sink.write({
            "event": "status",
            "data": {"status": "generating", "message": "Generating response..."},
        })

        final_results = self._collector.final_results
        virtual_record_map = self._collector.virtual_records
        tool_results = state.get("all_tool_results", [])
        query = state.get("query", "")
        org_id = self._context.org_id

        if final_results and virtual_record_map:
            state["qna_message_content"] = self._build_qna_message_content(
                final_results, virtual_record_map, query
            )
        else:
            state["qna_message_content"] = None

        messages = await create_response_messages(state)

        attachment_blocks = await _ensure_attachment_blocks(state, log)
        _inject_attachment_blocks(messages, attachment_blocks)

        non_retrieval_results = [
            r for r in tool_results
            if r.get("status") == "success" and "retrieval" not in r.get("tool_name", "").lower()
        ]
        failed_results = [r for r in tool_results if r.get("status") == "error"]
        has_api_results = bool(non_retrieval_results) or (
            bool(failed_results) and not any(r.get("status") == "success" for r in tool_results)
        )

        if has_api_results:
            await self._append_tool_results_context(messages, tool_results, final_results, state)

        prior_web_records = _extract_web_records_from_tool_results(tool_results, org_id)

        tools, tool_runtime_kwargs = self._build_respond_tools(state, virtual_record_map, messages)

        answer_text = ""
        citations: list[Any] = []
        reason = None
        confidence = None
        reference_data: list[Any] = []
        captured_web_records: list[dict[str, Any]] = list(prior_web_records)

        ref_mapper = self._collector.citation_ref_mapper

        async for stream_event in stream_llm_response_with_tools(
            llm=self._context.llm,
            messages=messages,
            final_results=final_results,
            all_queries=[query] if query else [],
            retrieval_service=self._context.retrieval_service,
            user_id=self._context.user_id,
            org_id=org_id,
            virtual_record_id_to_result=virtual_record_map,
            blob_store=self._context.blob_store,
            is_multimodal_llm=self._context.is_multimodal_llm,
            context_length=_DEFAULT_CONTEXT_LENGTH,
            tools=tools,
            tool_runtime_kwargs=tool_runtime_kwargs,
            target_words_per_chunk=1,
            mode="json",
            conversation_id=self._context.conversation_id,
            is_service_account=self._context.is_service_account,
            ref_mapper=ref_mapper,
            initial_web_records=prior_web_records,
        ):
            event_type = stream_event.get("event")
            event_data = stream_event.get("data", {})

            if event_type == "tool_execution_complete":
                captured_web_records = event_data.get("web_records", []) or captured_web_records
                continue

            if (
                event_type == "complete"
                and (final_results or captured_web_records)
                and not event_data.get("citations")
            ):
                event_data = self._enrich_citations(
                    event_data, final_results, virtual_record_map, captured_web_records, ref_mapper, log
                )

            if event_type == "complete" and event_data.get("referenceData"):
                event_data = {
                    **event_data,
                    "referenceData": normalize_reference_data_items(event_data["referenceData"]),
                }

            await self._emit_ask_user_question_fallback(state, event_sink)
            await event_sink.write({"event": event_type, "data": event_data})

            if event_type == "complete":
                answer_text = event_data.get("answer", "")
                citations = event_data.get("citations", [])
                reason = event_data.get("reason")
                confidence = event_data.get("confidence")
                reference_data = event_data.get("referenceData", []) or []

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
            "reason": reason,
            "confidence": confidence,
        }
        if reference_data:
            completion_data["referenceData"] = reference_data
        completion_data.update(_tool_names_and_results_from_state(state))
        state["response"] = answer_text
        state["completion_data"] = completion_data
        log.info("RespondPipeline: generated response (%d chars, %d citations)", len(answer_text), len(citations))
        return completion_data

    def _build_qna_message_content(
        self, final_results: list[Any], virtual_record_map: dict[str, Any], query: str
    ) -> Any:  # noqa: ANN401
        from app.utils.chat_helpers import get_message_content

        state = self._context.tool_state
        user_info = self._context.user_info or {}
        org_info = self._context.org_info or {}
        user_data = ""
        if user_info:
            account_type = (org_info.get("accountType") or "") if org_info else ""
            possessive = (
                "I am the user of the organization. "
                if account_type in ("Enterprise", "Business")
                else "I am the user. "
            )
            user_data = (
                f"{possessive}My name is {user_info.get('fullName', 'a user')} "
                f"({user_info.get('designation', '')})"
                + (f" from {org_info.get('name', 'the organization')}. " if org_info.get("name") else ". ")
                + "Please provide accurate and relevant information."
            )

        ref_mapper = self._collector.citation_ref_mapper or CitationRefMapper()
        qna_content, ref_mapper = get_message_content(
            final_results,
            virtual_record_map,
            user_data,
            query,
            "json",
            is_multimodal_llm=self._context.is_multimodal_llm,
            ref_mapper=ref_mapper,
            has_sql_connector=self._context.has_sql_connector and self._context.has_sql_knowledge,
            has_slack_connector=self._context.has_slack_connector and self._context.has_slack_knowledge,
        )
        state["citation_ref_mapper"] = ref_mapper
        return qna_content

    async def _append_tool_results_context(
        self,
        messages: list[HumanMessage],
        tool_results: list[dict[str, Any]],
        final_results: list[Any],
        state: dict[str, Any],
    ) -> None:
        from langchain_core.messages import HumanMessage as _HumanMessage

        from app.modules.agents.context.tool_results_context import (
            _build_tool_results_context,
        )

        qna_has_retrieval = bool(state.get("qna_message_content"))
        context_text = await _build_tool_results_context(
            tool_results,
            [] if qna_has_retrieval else final_results,
            has_retrieval_in_context=qna_has_retrieval,
            ref_mapper=self._collector.citation_ref_mapper,
            config_service=self._context.config_service,
            is_multimodal_llm=self._context.is_multimodal_llm,
            has_attachments=bool(state.get("attachments")),
        )
        if not context_text.strip():
            return
        if messages and isinstance(messages[-1], _HumanMessage):
            last_content = messages[-1].content
            if isinstance(last_content, list):
                last_content.append({"type": "text", "text": context_text})
            else:
                messages[-1].content = last_content + context_text
        else:
            messages.append(_HumanMessage(content=context_text))

    def _build_respond_tools(
        self, state: dict[str, Any], virtual_record_map: dict[str, Any], messages: list[Any]
    ) -> tuple[list[Any], dict[str, Any]]:
        tools: list[Any] = []
        if virtual_record_map:
            from app.utils.fetch_full_record import create_fetch_full_record_tool

            tools.append(
                create_fetch_full_record_tool(
                    virtual_record_map, org_id=self._context.org_id, graph_provider=self._context.graph_provider
                )
            )

        has_web_search_tool = False
        try:
            from app.modules.agents.qna.tool_system import _create_web_tools

            web_tools = _create_web_tools(state)
            tools.extend(web_tools)
            has_web_search_tool = any(getattr(t, "name", "") == "web_search" for t in web_tools)
        except Exception as exc:
            logger.warning("Failed to add web tools to RespondPipeline: %s", exc)

        if has_web_search_tool and messages:
            from langchain_core.messages import SystemMessage as _SystemMessage

            web_tool_hint = (
                "\n\n## Web Tools Available (CRITICAL — READ BEFORE RESPONDING)\n"
                "You have `web_search` and `fetch_url` tools available.\n\n"
                "**MANDATORY RULE**: If the retrieved knowledge blocks above do NOT contain "
                "sufficient information to answer the user's question, you MUST use "
                "`web_search` (and/or `fetch_url` for specific URLs) to find the answer "
                "from the web BEFORE responding. Always attempt a web search first.\n\n"
            )
            if messages and isinstance(messages[0], _SystemMessage):
                existing = messages[0].content
                if isinstance(existing, list):
                    messages[0] = _SystemMessage(content=[*existing, {"type": "text", "text": web_tool_hint}])
                else:
                    messages[0] = _SystemMessage(content=existing + web_tool_hint)
            else:
                messages.insert(0, _SystemMessage(content=web_tool_hint))

        tool_runtime_kwargs = {
            "blob_store": self._context.blob_store,
            "graph_provider": self._context.graph_provider,
            "org_id": self._context.org_id,
            "conversation_id": self._context.conversation_id,
            "config_service": self._context.config_service,
        }
        return tools, tool_runtime_kwargs

    @staticmethod
    def _enrich_citations(
        event_data: dict[str, Any],
        final_results: list[Any],
        virtual_record_map: dict[str, Any],
        captured_web_records: list[dict[str, Any]],
        ref_mapper: Any,  # noqa: ANN401
        log: logging.Logger,
    ) -> dict[str, Any]:
        raw_answer = event_data.get("answer", "")
        if not raw_answer:
            return event_data
        try:
            ref_to_url = ref_mapper.ref_to_url if ref_mapper else None
            _, enriched = normalize_citations_and_chunks_for_agent(
                raw_answer, final_results, virtual_record_map, [],
                ref_to_url=ref_to_url, web_records=captured_web_records,
            )
        except Exception as exc:
            log.debug("RespondPipeline citation enrichment error: %s", exc)
            return event_data
        return {**event_data, "citations": enriched} if enriched else event_data

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
