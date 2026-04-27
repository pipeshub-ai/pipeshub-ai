"""
Deep Agent Respond Node - Dedicated response generator for the deep agent.

ROOT CAUSE (why a dedicated node is needed):
    LangGraph StateGraph filters state keys based on node function type annotations.
    The shared respond_node in qna/nodes.py is typed as `ChatState`, so LangGraph
    strips all DeepAgentState-only keys (completed_tasks, sub_agent_analyses, etc.)
    before passing state to it. This node is typed as `DeepAgentState`, so it
    receives ALL state keys correctly.

Pipeline:
    1. Collects analyses from sub_agent_analyses + completed_tasks
    2. Includes raw tool results as supplementary data for links/details
    3. Fast-path for API-only results (no retrieval)
    4. Full pipeline with stream_llm_response_with_tools for retrieval+citations
    5. Extracts reference links for the frontend
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.modules.agents.capability_summary import build_capability_summary
from app.modules.agents.deep.context_manager import (
    build_respond_conversation_context,
)
from app.modules.agents.qna.chat_state import is_custom_agent_system_prompt
from app.modules.agents.qna.stream_utils import safe_stream_write
from app.modules.qna.response_prompt import build_direct_answer_time_context

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.runnables import RunnableConfig
    from langgraph.types import StreamWriter

    from app.modules.agents.deep.state import DeepAgentState

logger = logging.getLogger(__name__)

_MAX_TOTAL_ANALYSES_CHARS = 100_000
_SUBSTANTIAL_RESPONSE_CHARS = 500
_MAX_URL_EXTRACT_DEPTH = 3


# ---------------------------------------------------------------------------
# Main respond node
# ---------------------------------------------------------------------------

async def deep_respond_node(
    state: DeepAgentState,
    config: RunnableConfig,
    writer: StreamWriter,
) -> DeepAgentState:
    """
    Generate the final response for the deep agent pipeline.

    This node is typed as DeepAgentState (not ChatState) so LangGraph passes
    ALL state keys including completed_tasks, sub_agent_analyses, etc.

    Wraps _deep_respond_impl with a top-level safety net: if anything in the
    message-building section (imports, retrieval merge, prompt construction)
    throws an unhandled exception, we still send error events to the frontend
    instead of silently dying.
    """
    start_time = time.perf_counter()
    log = state.get("logger", logger)
    try:
        return await _deep_respond_impl(state, config, writer, start_time, log)
    except Exception as e:
        log.error("deep_respond_node unhandled error: %s", e, exc_info=True)
        error_msg = "I encountered an issue generating the response. Please try again."
        error_response = {
            "answer": error_msg,
            "citations": [],
            "confidence": "Low",
            "answerMatchType": "Error",
        }
        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {"chunk": error_msg, "accumulated": error_msg, "citations": []},
        }, config)
        safe_stream_write(writer, {"event": "complete", "data": error_response}, config)
        state["response"] = error_msg
        state["completion_data"] = error_response
        duration_ms = (time.perf_counter() - start_time) * 1000
        log.info("deep_respond_node (error recovery): %.0fms", duration_ms)
        return state


async def _deep_respond_impl(
    state: DeepAgentState,
    config: RunnableConfig,
    writer: StreamWriter,
    start_time: float,
    log: logging.Logger,
) -> DeepAgentState:
    """Core implementation of deep_respond_node."""
    llm = state.get("llm")

    # ---------------------------------------------------------------
    # Diagnostic: log state keys available to this node
    # ---------------------------------------------------------------
    _log_state_diagnostic(state, log)

    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "generating", "message": "Generating response..."},
    }, config)

    # ---------------------------------------------------------------
    # Handle error state
    # ---------------------------------------------------------------
    if state.get("error"):
        return _handle_error_state(state, writer, config, log)

    # ---------------------------------------------------------------
    # Handle clarification
    # ---------------------------------------------------------------
    reflection_decision = state.get("reflection_decision", "respond_success")
    reflection = state.get("reflection", {})

    if reflection_decision == "respond_clarify":
        return _handle_clarify(state, reflection, writer, config, log)

    # ---------------------------------------------------------------
    # Handle error decision from aggregator
    # ---------------------------------------------------------------
    if reflection_decision == "respond_error":
        return _handle_error_decision(state, reflection, writer, config, log)

    # ---------------------------------------------------------------
    # Handle direct answer (orchestrator decided no tools needed)
    # ---------------------------------------------------------------
    task_plan = state.get("task_plan") or {}
    if task_plan.get("can_answer_directly"):
        return await _handle_direct_answer(state, llm, writer, config, log)

    # ---------------------------------------------------------------
    # Main path: collect ALL available data
    # ---------------------------------------------------------------
    analyses = _collect_analyses(state, log)
    tool_results = _collect_tool_results(state, log)

    if not analyses and not tool_results:
        log.warning("deep_respond_node: no analyses or tool results, generating fallback")
        return await _handle_no_data(state, llm, writer, config, log)

    log.info(
        "deep_respond_node: %d analyses, %d tool results — generating response",
        len(analyses), len(tool_results),
    )

    query = state.get("query", "")
    all_tool_results = state.get("all_tool_results") or state.get("tool_results") or []
    final_results = state.get("final_results", [])
    virtual_record_map = state.get("virtual_record_id_to_result", {})

    # ================================================================
    # FAST PATH: API-only results with sub-agent analyses
    # When there are no retrieval results (no citations needed) and
    # sub-agents already produced analyses, use a lightweight LLM call.
    # ================================================================
    log.info(
        "Fast-path check: analyses=%d, final_results=%d, virtual_map=%d, tool_results=%d",
        len(analyses), len(final_results), len(virtual_record_map), len(all_tool_results),
    )
    if analyses and not final_results and not virtual_record_map:
        log.info("Fast-path: API-only results with sub-agent analysis, using lightweight response")
        try:
            from app.modules.agents.qna.nodes import _generate_fast_api_response
            result = await _generate_fast_api_response(
                state, llm, query, all_tool_results, analyses, log, writer, config,
            )
            if result:
                # Supplement referenceData with deep-agent extraction
                completion_data = state.get("completion_data", {})
                if completion_data and not completion_data.get("referenceData"):
                    deep_refs = _extract_reference_links(analyses, tool_results)
                    if deep_refs:
                        completion_data["referenceData"] = deep_refs
                        state["completion_data"] = completion_data

                duration_ms = (time.perf_counter() - start_time) * 1000
                log.info("deep_respond_node (fast-path): %.0fms", duration_ms)
                return state
        except Exception as e:
            log.warning("Fast-path failed, falling back to standard: %s", e)
            # Fall through to standard path

    log.info("Citation data: %d results, %d records", len(final_results), len(virtual_record_map))

    # ================================================================
    # Build qna_message_content using get_message_content() — identical
    # to what the chatbot uses — for consistent R-label block numbers.
    # ================================================================
    if final_results and virtual_record_map:
        from app.utils.chat_helpers import get_message_content as _get_msg_content

        user_data = ""
        user_info = state.get("user_info") or {}
        org_info = state.get("org_info") or {}
        if user_info:
            account_type = (org_info.get("accountType") or "") if org_info else ""
            if account_type in ("Enterprise", "Business"):
                user_data = (
                    "I am the user of the organization. "
                    f"My name is {user_info.get('fullName', 'a user')} "
                    f"({user_info.get('designation', '')}) "
                    f"from {org_info.get('name', 'the organization')}. "
                    "Please provide accurate and relevant information."
                )
            else:
                user_data = (
                    "I am the user. "
                    f"My name is {user_info.get('fullName', 'a user')} "
                    f"({user_info.get('designation', '')}). "
                    "Please provide accurate and relevant information."
                )

        from app.utils.chat_helpers import CitationRefMapper as _CitationRefMapper
        _ref_mapper = state.get("citation_ref_mapper") or _CitationRefMapper()
        qna_content, _ref_mapper = _get_msg_content(
            final_results, virtual_record_map, user_data, query, "json",is_multimodal_llm=state.get("is_multimodal_llm", False), ref_mapper=_ref_mapper, has_sql_connector=state.get("has_sql_connector", False) and state.get("has_sql_knowledge", False),
        )
        state["citation_ref_mapper"] = _ref_mapper
        state["qna_message_content"] = qna_content
        log.debug("Built qna_message_content via get_message_content()")
    else:
        state["qna_message_content"] = None

    # ================================================================
    # Build messages.
    #
    # KEY INSIGHT: The chatbot uses a 1-line system prompt and relies
    # on get_message_content() for all instructions (citation rules,
    # output format, tool guidance). It produces consistently better
    # responses than the agent's 248-line system prompt from
    # build_response_prompt(), which duplicates instructions already
    # in the user message and over-constrains the LLM.
    #
    # For RETRIEVAL queries (qna_message_content present):
    #   → Use a simple system prompt (chatbot-style)
    #   → The user message from get_message_content() has everything
    #
    # For NON-RETRIEVAL or MIXED queries:
    #   → Use create_response_messages (full system prompt)
    #   → Append sub-agent analyses and API tool results
    # ================================================================
    qna_has_retrieval = bool(state.get("qna_message_content"))

    if qna_has_retrieval:
        # ── RETRIEVAL PATH: simple system prompt (chatbot-aligned) ──
        log.info("deep_respond_node: using simple system prompt (retrieval path)")
        messages = _build_simple_retrieval_messages(state, log)
    else:
        # ── NON-RETRIEVAL / MIXED PATH: full system prompt ──────────
        from app.modules.qna.response_prompt import create_response_messages
        messages = create_response_messages(state)

    # Append non-retrieval tool results + sub-agent analyses context
    non_retrieval_results = [
        r for r in all_tool_results
        if r.get("status") == "success"
        and "retrieval" not in r.get("tool_name", "").lower()
    ]
    failed_results = [r for r in all_tool_results if r.get("status") == "error"]

    has_api_results = non_retrieval_results or (
        failed_results and not any(r.get("status") == "success" for r in all_tool_results)
    )

    if has_api_results or analyses:
        from app.modules.agents.qna.nodes import _build_tool_results_context

        context = _build_tool_results_context(
            all_tool_results,
            [] if qna_has_retrieval else final_results,
            has_retrieval_in_context=qna_has_retrieval,
        ) if has_api_results else ""

        # Prepend sub-agent analyses as supplementary structured context.
        # Even when raw retrieval blocks exist in qna_message_content,
        # analyses add structured insights that make deep mode responses
        # richer and more detailed than quick/verification modes.
        if analyses:
            trimmed_analyses = _trim_analyses_to_budget(analyses, log)
            analyses_text = "\n## Sub-Agent Analysis\n\n"
            analyses_text += (
                "The following detailed analysis was produced by specialized sub-agents "
                "that studied the raw data in depth. "
            )
            if qna_has_retrieval:
                analyses_text += (
                    "Use these structured insights to build a COMPREHENSIVE, WELL-SYNTHESIZED "
                    "response. Do NOT simply list findings — instead:\n"
                    "  • Identify the key themes and cross-source connections the analyses reveal.\n"
                    "  • For each topic in the user's query, draw from the relevant analyses AND "
                    "the context blocks to construct a detailed, coherent explanation.\n"
                    "  • Where multiple sub-agents investigated the same topic, merge their findings "
                    "into a single, richer narrative rather than repeating similar content.\n"
                    "  • Cover ALL distinct aspects of the user's query using the evidence available; "
                    "if a topic is thoroughly documented, reflect that depth in your answer.\n\n"
                )
            elif has_api_results:
                analyses_text += (
                    "Preserve ALL items and findings from this analysis in your response. "
                    "Cross-reference with the raw API data above for exact details, links, "
                    "and any additional information.\n\n"
                )
            else:
                analyses_text += (
                    "Preserve ALL items and findings from this analysis in your response. "
                    "Include every data point, link, and detail.\n\n"
                )
            for analysis in trimmed_analyses[:5]:
                analyses_text += f"{analysis}\n\n"
            context = analyses_text + context

        if context.strip():
            if messages and isinstance(messages[-1], HumanMessage):
                last_content = messages[-1].content
                if isinstance(last_content, list):
                    last_content.append({"type": "text", "text": context})
                else:
                    messages[-1].content = last_content + context
            else:
                messages.append(HumanMessage(content=context))

    # Log prompt size
    total_chars = sum(
        len(m.content) if isinstance(m.content, str)
        else sum(len(p.get("text", "")) for p in m.content if isinstance(p, dict))
        if isinstance(m.content, list) else 0
        for m in messages
    )
    log.info("deep_respond_node: prompt built, %d chars total", total_chars)

    # ================================================================
    # Setup tools (fetch_full_record for retrieval)
    # ================================================================
    tools: list = []
    if virtual_record_map:
        from app.utils.fetch_full_record import (
            create_fetch_full_record_tool,
        )
        fetch_tool = create_fetch_full_record_tool(
            virtual_record_map,
            org_id=state.get("org_id", ""),
            graph_provider=state.get("graph_provider"),
        )
        tools = [fetch_tool]
        log.debug(
            "Added agent fetch_full_record tool (%d records, %d labels)",
            len(virtual_record_map),
            len(final_results),
        )

    # Initialize blob_store if missing
    graph_provider = state.get("graph_provider")
    blob_store = state.get("blob_store")
    if blob_store is None:
        try:
            from app.modules.transformers.blob_storage import BlobStorage
            config_svc = state.get("config_service")
            blob_store = BlobStorage(
                logger=log,
                config_service=config_svc,
                graph_provider=graph_provider,
            )
            state["blob_store"] = blob_store
        except Exception as _bs_err:
            log.warning("Could not initialise BlobStorage: %s", _bs_err)

    tool_runtime_kwargs = {
        "blob_store": blob_store,
        "graph_provider": graph_provider,
        "org_id": state.get("org_id", ""),
        "conversation_id": state.get("conversation_id"),
    }

    # Construct all_queries — prefer decomposed_queries from planner,
    # fall back to task descriptions from the orchestrator plan, then
    # just the original query.
    decomposed_queries = state.get("decomposed_queries", [])
    if decomposed_queries:
        all_queries = [
            q.get("query", query)
            for q in decomposed_queries
            if isinstance(q, dict) and q.get("query")
        ]
        if not all_queries:
            all_queries = [query]
    else:
        # Build from orchestrator task descriptions for richer context
        _tasks = (task_plan.get("tasks") or []) if task_plan else []
        all_queries = [query]
        for t in _tasks:
            desc = t.get("description", "")
            if desc and desc != query:
                all_queries.append(desc)

    # ================================================================
    # Stream with tools (full pipeline — citations, tool execution)
    # ================================================================
    try:
        from app.utils.streaming import stream_llm_response_with_tools

        log.info("Using stream_llm_response_with_tools...")

        DEFAULT_CONTEXT_LENGTH = 128000

        answer_text = ""
        citations: list = []
        reason = None
        confidence = None
        reference_data: list = []
        any_chunks_sent = False  # Track whether we already sent chunks to the client

        async for stream_event in stream_llm_response_with_tools(
            llm=llm,
            messages=messages,
            final_results=final_results,
            all_queries=all_queries,
            retrieval_service=state.get("retrieval_service"),
            user_id=state.get("user_id", ""),
            org_id=state.get("org_id", ""),
            virtual_record_id_to_result=virtual_record_map,
            blob_store=blob_store,
            is_multimodal_llm=state.get("is_multimodal_llm", False),
            context_length=DEFAULT_CONTEXT_LENGTH,
            tools=tools,
            tool_runtime_kwargs=tool_runtime_kwargs,
            target_words_per_chunk=1,
            mode="json",
            is_agent=True,
            conversation_id=state.get("conversation_id"),
            ref_mapper=state.get("citation_ref_mapper"),
        ):
            event_type = stream_event.get("event")
            event_data = stream_event.get("data", {})

            # ── Citation enrichment (second-pass extraction) ──────────
            if (
                event_type == "complete"
                and final_results
                and not event_data.get("citations")
            ):
                _raw_answer = event_data.get("answer", "")
                _enriched: list = []
                if _raw_answer:
                    try:
                        from app.utils.citations import (
                            normalize_citations_and_chunks_for_agent as _ncc_agent,
                        )
                        _ref_to_url_snap = state.get("citation_ref_mapper")
                        _ref_to_url_snap = _ref_to_url_snap.ref_to_url if _ref_to_url_snap else None
                        _, _enriched = _ncc_agent(
                            _raw_answer, final_results, virtual_record_map, [],
                            ref_to_url=_ref_to_url_snap,
                        )
                        if _enriched:
                            log.info(
                                "Citation enrichment: extracted %d citations",
                                len(_enriched),
                            )
                    except Exception as _ce:
                        log.debug("Citation enrichment error: %s", _ce)
                if _enriched:
                    event_data = {**event_data, "citations": _enriched}
            # ──────────────────────────────────────────────────────────

            safe_stream_write(writer, {"event": event_type, "data": event_data}, config)

            if event_type == "answer_chunk":
                any_chunks_sent = True
            if event_type == "complete":
                answer_text = event_data.get("answer", "")
                citations = event_data.get("citations", [])
                reason = event_data.get("reason")
                confidence = event_data.get("confidence")
                reference_data = event_data.get("referenceData", [])

        # Handle empty response — only emit fallback events if no chunks
        # were already sent to the client (avoids duplicate/contradictory output)
        if not answer_text or not answer_text.strip():
            log.warning("Empty response (chunks_sent=%s), using fallback", any_chunks_sent)
            answer_text = _build_fallback_response(analyses) if analyses else (
                "I wasn't able to generate a response. Please try rephrasing."
            )
            fallback_response = {
                "answer": answer_text,
                "citations": [],
                "confidence": "Low",
                "answerMatchType": "Fallback Response",
            }
            if not any_chunks_sent:
                safe_stream_write(writer, {
                    "event": "answer_chunk",
                    "data": {"chunk": answer_text, "accumulated": answer_text, "citations": []},
                }, config)
                safe_stream_write(writer, {"event": "complete", "data": fallback_response}, config)
            state["response"] = answer_text
            state["completion_data"] = fallback_response
        else:
            completion_data = {
                "answer": answer_text,
                "citations": citations,
                "reason": reason,
                "confidence": confidence or "High",
            }
            # Merge reference data: pipeline's referenceData is primary,
            # supplement with deep-agent URL extraction as fallback
            if reference_data:
                completion_data["referenceData"] = reference_data
            else:
                deep_refs = _extract_reference_links(analyses, tool_results)
                if deep_refs:
                    completion_data["referenceData"] = deep_refs

            state["response"] = answer_text
            state["completion_data"] = completion_data

        log.info("Generated response: %d chars, %d citations", len(answer_text), len(citations))

    except Exception as e:
        log.error("Response generation failed: %s", e, exc_info=True)
        error_msg = "I encountered an issue. Please try again."
        error_response = {
            "answer": error_msg,
            "citations": [],
            "confidence": "Low",
            "answerMatchType": "Error",
        }
        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {"chunk": error_msg, "accumulated": error_msg, "citations": []},
        }, config)
        safe_stream_write(writer, {"event": "complete", "data": error_response}, config)
        state["response"] = error_msg
        state["completion_data"] = error_response

    duration_ms = (time.perf_counter() - start_time) * 1000
    log.info("deep_respond_node: %.0fms", duration_ms)
    return state


# ---------------------------------------------------------------------------
# Simple retrieval message builder (chatbot-aligned)
# ---------------------------------------------------------------------------

def _build_simple_retrieval_messages(
    state: DeepAgentState,
    log: logging.Logger,
) -> list:
    """Build messages for retrieval queries using a simple system prompt.

    Mirrors the chatbot approach: short system prompt + conversation history +
    qna_message_content as the user message. The user message (built by
    get_message_content) already contains all citation rules, output format,
    tool instructions, and blocks — no need for the 248-line
    system prompt from build_response_prompt().
    """
    messages: list = []

    # ── 1. System prompt (short, chatbot-style) ──────────────────────
    parts: list[str] = []

    # Include agent-level instructions if configured
    instructions = state.get("instructions", "")
    if instructions and instructions.strip():
        parts.append(f"## Agent Instructions\n{instructions.strip()}")

    system_prompt = state.get("system_prompt", "")
    if system_prompt and system_prompt.strip():
        parts.append(system_prompt.strip())

    # Core role — matches the chatbot's approach but tuned for deep agent
    parts.append(
        "You are an enterprise deep-research answering expert. "
        "You have access to context blocks (raw source data with citation labels) "
        "AND sub-agent analysis (structured insights from specialized agents that "
        "studied the data in depth).\n\n"
        "Your response should be MORE COMPREHENSIVE and MORE DETAILED than a standard "
        "retrieval answer. Specifically:\n"
        "- Synthesize the sub-agent analyses to identify key themes, connections between "
        "sources, and deeper insights that a surface-level read would miss.\n"
        "- Provide detailed explanations with specifics, not brief summaries.\n"
        "- When multiple sources cover the same topic, synthesize them into a coherent "
        "narrative rather than listing them separately.\n"
        "- Expand on each point with specific details found in the blocks and analysis.\n\n"
        "CRITICAL SYNTHESIS RULES:\n"
        "- The sub-agent analyses ARE your primary knowledge source. They were produced "
        "by specialized agents that fetched and studied the full source documents. "
        "If an analysis covers a topic, you HAVE that information — present it.\n"
        "- NEVER say 'I don't have information about X' or 'there is no content for X' "
        "if the sub-agent analyses discuss X. Extract and present what the analyses reveal.\n"
        "- If a specific document (e.g. an ERD page, a schema doc) is mentioned in the "
        "analyses but its full text is not in the context blocks, summarise what the "
        "analyses say about it and cite the source appropriately.\n"
        "- Answer EVERY part of the user's query. If one part is covered only in the "
        "analyses and another only in the blocks, combine both into a unified answer.\n\n"
        "CITATION RULES:\n"
        "- **Limit citations to the most relevant blocks.** Do NOT cite every sentence — only cite the most important, non-obvious, or specific factual claims.\n"
        "- Each block has a 'Citation ID' (e.g., ref1, ref2) — use it exactly for citations: [source](ref1).\n"
        "- Use EXACTLY the Citation ID shown in the context. Do NOT invent or modify Citation IDs.\n"
        "- If you cannot find the Citation ID for a claim, omit the citation rather than guessing.\n\n"
    )

    messages.append(SystemMessage(content="\n\n".join(parts)))
    log.debug(
        "Simple retrieval system prompt: %d chars",
        len(messages[0].content),
    )

    # ── 2. Conversation context (summary + recent turns) ────────────
    # Uses compact context: summary for older turns (avoids flooding
    # with long previous bot responses) + last 3 pairs truncated.
    # Keeps the LLM focused on the retrieval blocks and analyses that follow.
    previous_conversations = state.get("previous_conversations", [])
    conv_messages = build_respond_conversation_context(
        previous_conversations,
        state.get("conversation_summary"),
        log,
    )
    if conv_messages:
        messages.extend(conv_messages)

    # ── 3. Current user message (qna_message_content) ────────────────
    qna_content = state.get("qna_message_content")
    if qna_content:
        messages.append(HumanMessage(content=qna_content))
    else:
        # Shouldn't happen (caller checks), but fallback to raw query
        messages.append(HumanMessage(content=state.get("query", "")))

    return messages


# ---------------------------------------------------------------------------
# Analyses budget trimming
# ---------------------------------------------------------------------------

def _trim_analyses_to_budget(
    analyses: list[str],
    log: logging.Logger,
    budget: int = _MAX_TOTAL_ANALYSES_CHARS,
) -> list[str]:
    """Proportionally trim analyses to fit within budget."""
    total = sum(len(a) for a in analyses)
    if total <= budget:
        return analyses
    log.info("Trimming analyses from %d to %d chars (budget)", total, budget)
    trimmed = []
    for a in analyses:
        share = int(budget * len(a) / total)
        if len(a) > share:
            trimmed.append(a[:share] + "\n... [trimmed for context budget]")
        else:
            trimmed.append(a)
    return trimmed


# ---------------------------------------------------------------------------
# Diagnostic logging
# ---------------------------------------------------------------------------

def _log_state_diagnostic(state: DeepAgentState, log: logging.Logger) -> None:
    """Log which deep-agent keys are present and their sizes."""
    diag = {}

    completed = state.get("completed_tasks")
    if completed:
        success = sum(1 for t in completed if t.get("status") == "success")
        error = sum(1 for t in completed if t.get("status") == "error")
        diag["completed_tasks"] = f"{len(completed)} ({success} ok, {error} err)"
    else:
        diag["completed_tasks"] = "EMPTY"

    analyses = state.get("sub_agent_analyses")
    diag["sub_agent_analyses"] = len(analyses) if analyses else "EMPTY"

    tools = state.get("tool_results")
    diag["tool_results"] = len(tools) if tools else "EMPTY"

    diag["reflection_decision"] = state.get("reflection_decision", "NOT_SET")

    plan = state.get("task_plan")
    diag["task_plan"] = "present" if plan else "EMPTY"

    final_results = state.get("final_results")
    diag["final_results"] = len(final_results) if final_results else "EMPTY"

    virtual_map = state.get("virtual_record_id_to_result")
    diag["virtual_record_map"] = len(virtual_map) if virtual_map else "EMPTY"

    log.info("deep_respond_node state: %s", diag)


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def _collect_analyses(state: DeepAgentState, log: logging.Logger) -> list[str]:
    """
    Collect analyses from all available sources.

    Priority:
    1. sub_agent_analyses (pre-built by execute_sub_agents_node)
    2. Rebuild from completed_tasks (fallback)
    """
    # Source 1: sub_agent_analyses
    analyses = state.get("sub_agent_analyses") or []
    if analyses:
        log.info("Collected %d analyses from sub_agent_analyses", len(analyses))
        return list(analyses)

    # Source 2: rebuild from completed_tasks
    completed = state.get("completed_tasks") or []
    if not completed:
        log.warning("No sub_agent_analyses and no completed_tasks in state")
        return []

    log.info("Rebuilding analyses from %d completed_tasks", len(completed))
    rebuilt = []
    for task in completed:
        if task.get("status") != "success":
            continue

        task_id = task.get("task_id", "unknown")
        domains = ", ".join(task.get("domains", []))
        label = f"[{task_id} ({domains})]"

        # Complex tasks: use the consolidated domain summary
        domain_summary = task.get("domain_summary")
        if domain_summary:
            rebuilt.append(f"{label}: {domain_summary}")
            continue

        # Simple tasks: use the response text
        task_result = task.get("result", {})
        if isinstance(task_result, dict):
            response_text = task_result.get("response", "")
            if response_text:
                rebuilt.append(f"{label}: {response_text}")

    if rebuilt:
        log.info("Rebuilt %d analyses from completed_tasks", len(rebuilt))
    else:
        log.warning("completed_tasks present but no usable data found")

    return rebuilt


def _collect_tool_results(state: DeepAgentState, log: logging.Logger) -> list[dict[str, Any]]:
    """
    Collect raw tool results for supplementary data (links, exact values).

    SMART CONSOLIDATION: Skip raw tool results when analyses already contain
    substantial formatted data for the same domains. Raw JSON injected
    alongside well-formatted analyses confuses the LLM and produces corrupted
    tables (e.g., every column filled with the title link).

    Rules:
    1. Complex tasks with domain_summary → always skip raw results for that domain
    2. Simple tasks with substantial analyses (>500 chars) → skip raw results
       for that domain (the sub-agent already formatted the data well)
    3. Only include raw results when analyses are missing or very short
    """
    completed = state.get("completed_tasks") or []
    state.get("sub_agent_analyses") or []

    # Build set of domains that already have comprehensive data in analyses.
    covered_domains: set = set()

    for t in completed:
        if t.get("status") != "success":
            continue
        task_domains = [d.lower() for d in t.get("domains", [])]

        # Complex tasks with domain_summary → always covered
        if t.get("domain_summary"):
            for d in task_domains:
                covered_domains.add(d)
            continue

        # Simple tasks: check if the analysis is substantial enough
        task_result = t.get("result", {})
        response_text = ""
        if isinstance(task_result, dict):
            response_text = task_result.get("response", "")

        # If the sub-agent produced a substantial formatted response (>500 chars),
        # the raw API data would just be redundant and confuse the LLM.
        if len(response_text) > _SUBSTANTIAL_RESPONSE_CHARS:
            for d in task_domains:
                covered_domains.add(d)

    if covered_domains:
        log.info("Domains covered by analyses (skipping raw results): %s",
                 ", ".join(sorted(covered_domains)))

    # If ALL domains are covered, skip raw results entirely
    all_domains = set()
    for t in completed:
        if t.get("status") == "success":
            for d in t.get("domains", []):
                all_domains.add(d.lower())

    if all_domains and all_domains <= covered_domains:
        log.info("All %d domains covered by analyses — skipping all raw tool results",
                 len(all_domains))
        return []

    all_results = state.get("tool_results") or state.get("all_tool_results") or []
    if not all_results:
        return []

    useful = []
    for r in all_results:
        if r.get("status") != "success":
            continue
        tool_name = r.get("tool_name", "")
        # Skip retrieval results (they don't have API data with links)
        if "retrieval" in tool_name.lower() or "knowledge" in tool_name.lower():
            continue
        # Skip results from domains that already have comprehensive analyses
        if covered_domains:
            tool_domain = tool_name.split(".")[0].lower() if "." in tool_name else ""
            if tool_domain and tool_domain in covered_domains:
                continue
        useful.append(r)

    if useful:
        log.info("Collected %d useful tool results for supplementary data", len(useful))

    return useful


# ---------------------------------------------------------------------------
# Fallback response (when LLM returns empty)
# ---------------------------------------------------------------------------

def _build_fallback_response(analyses: list[str]) -> str:
    """Build a fallback response directly from analyses when LLM fails."""
    parts = ["Here's what I found:\n"]
    for analysis in analyses:
        # Strip the [task_id (domains)]: prefix for cleaner output
        content = analysis.split("]: ", 1)[1] if "]: " in analysis else analysis
        parts.append(content)
    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# Reference link extraction
# ---------------------------------------------------------------------------

_URL_PATTERN = re.compile(r'https?://[^\s\)\]>]+')


def _extract_reference_links(
    analyses: list[str],
    tool_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract unique URLs from analyses and tool results for frontend referenceData."""
    seen: set = set()
    links: list[dict[str, Any]] = []

    # From analyses text
    for text in analyses:
        for url in _URL_PATTERN.findall(text):
            cleaned_url = url.rstrip(".,;:!?\"'")
            if cleaned_url not in seen:
                seen.add(cleaned_url)
                links.append({"url": cleaned_url})

    # From tool results
    for r in tool_results:
        _extract_urls_from_value(r.get("result", ""), seen, links)

    return links[:100]


def _extract_urls_from_value(value: object, seen: set, links: list, depth: int = 0) -> None:
    """Recursively extract URLs from tool result values."""
    if depth > _MAX_URL_EXTRACT_DEPTH:
        return

    if isinstance(value, str):
        for url in _URL_PATTERN.findall(value):
            cleaned_url = url.rstrip(".,;:!?\"'")
            if cleaned_url not in seen:
                seen.add(cleaned_url)
                links.append({"url": cleaned_url})
    elif isinstance(value, dict):
        # Check common URL fields first
        url_fields = ("url", "webLink", "webViewLink", "htmlUrl", "permalink",
                       "link", "href", "self", "joinUrl", "joinWebUrl")
        for field in url_fields:
            val = value.get(field)
            if isinstance(val, str) and val.startswith("http") and val not in seen:
                seen.add(val)
                links.append({"url": val})
        # Recurse into values
        for v in value.values():
            _extract_urls_from_value(v, seen, links, depth + 1)
    elif isinstance(value, list):
        for item in value[:20]:  # Cap list traversal
            _extract_urls_from_value(item, seen, links, depth + 1)


# ---------------------------------------------------------------------------
# Special case handlers
# ---------------------------------------------------------------------------

def _handle_error_state(
    state: DeepAgentState,
    writer: StreamWriter,
    config: RunnableConfig,
    log: logging.Logger,
) -> DeepAgentState:
    """Handle pre-existing error in state."""
    error = state["error"]
    error_msg = error.get("message", error.get("detail", "An error occurred"))
    completion = {
        "answer": error_msg,
        "citations": [],
        "confidence": "Low",
        "answerMatchType": "Error",
    }
    safe_stream_write(writer, {
        "event": "answer_chunk",
        "data": {"chunk": error_msg, "accumulated": error_msg, "citations": []},
    }, config)
    safe_stream_write(writer, {"event": "complete", "data": completion}, config)
    state["response"] = error_msg
    state["completion_data"] = completion
    return state


def _handle_clarify(
    state: DeepAgentState,
    reflection: dict[str, Any],
    writer: StreamWriter,
    config: RunnableConfig,
    log: logging.Logger,
) -> DeepAgentState:
    """Handle clarification request."""
    question = reflection.get("clarifying_question", "Could you provide more details?")
    completion = {
        "answer": question,
        "citations": [],
        "confidence": "Medium",
        "answerMatchType": "Clarification Needed",
    }
    safe_stream_write(writer, {
        "event": "answer_chunk",
        "data": {"chunk": question, "accumulated": question, "citations": []},
    }, config)
    safe_stream_write(writer, {"event": "complete", "data": completion}, config)
    state["response"] = question
    state["completion_data"] = completion
    return state


def _handle_error_decision(
    state: DeepAgentState,
    reflection: dict[str, Any],
    writer: StreamWriter,
    config: RunnableConfig,
    log: logging.Logger,
) -> DeepAgentState:
    """Handle aggregator's error decision."""
    error_context = reflection.get("error_context", "")
    if error_context:
        error_msg = f"I wasn't able to complete that request. {error_context}\n\nPlease try again."
    else:
        error_msg = "I encountered errors while processing your request. Please try again."

    completion = {
        "answer": error_msg,
        "citations": [],
        "confidence": "Low",
        "answerMatchType": "Tool Execution Failed",
    }
    safe_stream_write(writer, {
        "event": "answer_chunk",
        "data": {"chunk": error_msg, "accumulated": error_msg, "citations": []},
    }, config)
    safe_stream_write(writer, {"event": "complete", "data": completion}, config)
    state["response"] = error_msg
    state["completion_data"] = completion
    return state


async def _handle_direct_answer(
    state: DeepAgentState,
    llm: BaseChatModel,
    writer: StreamWriter,
    config: RunnableConfig,
    log: logging.Logger,
) -> DeepAgentState:
    """Handle direct answer (no tools needed).

    Streams the LLM response to the frontend, then sends the completion event
    and stores the result in state for downstream graph nodes.
    """
    from app.utils.streaming import stream_llm_response

    query = state.get("query", "")

    instructions_prefix = ""
    agent_instructions = state.get("instructions")
    if agent_instructions and agent_instructions.strip():
        instructions_prefix = f"## Agent Instructions\n{agent_instructions.strip()}\n\n"

    role_prefix = ""
    persona = state.get("system_prompt")
    if is_custom_agent_system_prompt(persona):
        role_prefix = f"{persona.strip()}\n\n"

    system_content = (
        f"{instructions_prefix}{role_prefix}"
        "You are a helpful, friendly AI assistant. Respond naturally and concisely."
    )

    user_info = state.get("user_info") or {}
    org_info = state.get("org_info") or {}
    user_context = _format_user_context(user_info, org_info)
    user_content = query
    if user_context:
        user_content += f"\n\n{user_context}"
        system_content += "\n\nWhen the user asks about themselves, use the provided info DIRECTLY."

    # Add capability summary
    capability_summary = build_capability_summary(state)
    system_content += f"\n\n{capability_summary}"
    system_content += f"\n\n{build_direct_answer_time_context(state)}"

    messages = [SystemMessage(content=system_content)]

    # Include compact conversation context (summary + recent turns)
    previous = state.get("previous_conversations", [])
    if previous:
        messages.extend(build_respond_conversation_context(
            previous, state.get("conversation_summary"), log,
        ))

    messages.append(HumanMessage(content=user_content))

    answer_text = ""
    citations: list = []

    try:
        async for stream_event in stream_llm_response(
            llm=llm,
            messages=messages,
            final_results=[],
            logger=log,
            target_words_per_chunk=1,
            mode="simple",
        ):
            event_type = stream_event.get("event")
            event_data = stream_event.get("data", {})

            safe_stream_write(writer, {"event": event_type, "data": event_data}, config)

            if event_type == "complete":
                answer_text = event_data.get("answer", "")
                citations = event_data.get("citations", [])

    except Exception as e:
        log.error("Direct answer streaming failed: %s", e, exc_info=True)
        answer_text = (
            "I encountered an error while generating the response. "
            "Please try again."
        )
        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {"chunk": answer_text, "accumulated": answer_text, "citations": []},
        }, config)
        safe_stream_write(writer, {
            "event": "complete",
            "data": {"answer": answer_text, "citations": [], "confidence": "Low"},
        }, config)

    answer = answer_text.strip() or "I'm here to help! How can I assist you?"
    state["response"] = answer
    state["completion_data"] = {
        "answer": answer,
        "citations": citations,
        "confidence": "High",
        "answerMatchType": "Direct Response",
    }
    return state


async def _handle_no_data(
    state: DeepAgentState,
    llm: BaseChatModel,
    writer: StreamWriter,
    config: RunnableConfig,
    log: logging.Logger,
) -> DeepAgentState:
    """Handle case where no analyses or tool results are available."""
    completed = state.get("completed_tasks") or []
    error_tasks = [t for t in completed if t.get("status") == "error"]

    if error_tasks:
        error_details = []
        for t in error_tasks[:3]:
            tid = t.get("task_id", "unknown")
            err = t.get("error", "Unknown error")[:200]
            error_details.append(f"- **{tid}**: {err}")

        error_msg = (
            "I wasn't able to retrieve the data needed to answer your question.\n\n"
            "**Issues encountered:**\n"
            + "\n".join(error_details)
            + "\n\nPlease try again or rephrase your question."
        )
    else:
        error_msg = (
            "I wasn't able to find relevant data to answer your question. "
            "Please try rephrasing or providing more details."
        )

    completion = {
        "answer": error_msg,
        "citations": [],
        "confidence": "Low",
        "answerMatchType": "No Data Available",
    }
    safe_stream_write(writer, {
        "event": "answer_chunk",
        "data": {"chunk": error_msg, "accumulated": error_msg, "citations": []},
    }, config)
    safe_stream_write(writer, {"event": "complete", "data": completion}, config)
    state["response"] = error_msg
    state["completion_data"] = completion
    return state


# ---------------------------------------------------------------------------
# Helpers (kept for _handle_direct_answer)
# ---------------------------------------------------------------------------

def _format_user_context(user_info: dict, org_info: dict) -> str:
    """Format user info for the prompt."""
    user_email = (
        user_info.get("userEmail")
        or user_info.get("email")
        or ""
    )
    user_name = (
        user_info.get("fullName")
        or user_info.get("name")
        or user_info.get("displayName")
        or ""
    )

    if not user_email and not user_name:
        return ""

    parts = []
    if user_name:
        parts.append(f"Name: {user_name}")
    if user_email:
        parts.append(f"Email: {user_email}")
    if org_info.get("name"):
        parts.append(f"Organization: {org_info['name']}")

    return ", ".join(parts)

