"""
Deep Agent Integration Module

Bridges LangChain Deep Agents (deepagents) with PipesHub's tool ecosystem.
Creates a configured deep agent using PipesHub's LLM, retrieval services,
and connector-based toolset tools.

Architecture:
    PipesHub ChatState → deep_agent_node → create_deep_agent() → SSE Stream

The deep agent handles complex multi-step tasks with:
- Planning and task decomposition (built-in write_todos)
- Subagent spawning for context isolation
- Context management via virtual filesystem
- PipesHub tools (retrieval, Slack, Jira, etc.)
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter

from app.modules.agents.qna.chat_state import ChatState
from app.modules.agents.qna.stream_utils import safe_stream_write, stream_status

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

MAX_TOOL_RESULT_PREVIEW_LENGTH = 500
BLOCK_PATTERN = re.compile(r"\[R(\d+)-(\d+)\]")


# =============================================================================
# Tool Bridges — wrap PipesHub services as Deep Agent tools
# =============================================================================

def _build_retrieval_tool(state: ChatState):
    """
    Wrap PipesHub retrieval + reranker into a LangChain tool for Deep Agent.

    The tool searches the organisation's knowledge base (vector DB) and returns
    formatted results with citation metadata the response layer can use.
    """
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    retrieval_service = state["retrieval_service"]
    reranker_service = state.get("reranker_service")
    state.get("graph_provider")
    state.get("config_service")
    apps = state.get("apps", [])
    kb = state.get("kb", [])
    org_id = state.get("org_id", "")
    retrieval_mode = state.get("retrieval_mode", "HYBRID")
    log = state.get("logger", logger)

    class KnowledgeSearchInput(BaseModel):
        query: str = Field(description="Search query for the knowledge base")
        limit: int = Field(default=20, description="Maximum results to return (1-50)")

    async def _search(query: str, limit: int = 20) -> str:
        """Search the organisation's internal knowledge base for relevant documents."""
        try:
            search_results = await retrieval_service.search(
                query=query,
                org_id=org_id,
                limit=min(limit, 50),
                apps=apps if apps else None,
                kb=kb if kb else None,
                retrieval_mode=retrieval_mode,
            )

            if reranker_service and search_results:
                search_results = await reranker_service.rerank(
                    query=query,
                    documents=search_results,
                )

            if not search_results:
                return json.dumps({"status": "no_results", "message": "No relevant documents found."})

            formatted = []
            for idx, doc in enumerate(search_results):
                metadata = doc.get("metadata", {}) if isinstance(doc, dict) else getattr(doc, "metadata", {})
                content = doc.get("page_content", "") if isinstance(doc, dict) else getattr(doc, "page_content", "")
                source = metadata.get("source", metadata.get("title", f"Document-{idx+1}"))
                virtual_id = metadata.get("virtual_record_id", "")
                formatted.append({
                    "index": idx,
                    "label": f"R{idx+1}",
                    "source": source,
                    "content": content[:2000],
                    "virtual_record_id": virtual_id,
                })

            _update_retrieval_state(state, search_results, formatted, log)

            return json.dumps({
                "status": "success",
                "count": len(formatted),
                "results": formatted,
            }, default=str)

        except Exception as e:
            log.error(f"Knowledge search failed: {e}", exc_info=True)
            return json.dumps({"status": "error", "message": str(e)})

    return StructuredTool.from_function(
        func=_search,
        name="knowledge_search",
        description=(
            "Search the organisation's internal knowledge base (documents, wikis, "
            "files, etc.). Returns relevant passages with citation labels. "
            "Use this for questions about internal company information."
        ),
        args_schema=KnowledgeSearchInput,
        coroutine=_search,
    )


def _update_retrieval_state(
    state: ChatState,
    search_results: list,
    formatted: list,
    log: logging.Logger,
) -> None:
    """Update ChatState with retrieval results so the response layer can build citations."""
    existing = state.get("final_results") or []
    existing.extend(
        r if isinstance(r, dict) else {"page_content": getattr(r, "page_content", ""), "metadata": getattr(r, "metadata", {})}
        for r in search_results
    )
    state["final_results"] = existing

    vr_map = state.get("virtual_record_id_to_result") or {}
    label_map = state.get("record_label_to_uuid_map") or {}
    for item in formatted:
        vid = item.get("virtual_record_id", "")
        if vid:
            vr_map[vid] = item
            label_map[item["label"]] = vid
    state["virtual_record_id_to_result"] = vr_map
    state["record_label_to_uuid_map"] = label_map

    log.debug(f"Retrieval state updated: {len(existing)} total results, {len(vr_map)} virtual records")


def _build_toolset_tools(state: ChatState) -> List:
    """
    Convert PipesHub's registry-based toolset tools into LangChain StructuredTools
    that Deep Agent can use.

    Reuses the existing tool_system infrastructure.
    """
    from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

    log = state.get("logger", logger)

    try:
        all_tools = get_agent_tools_with_schemas(state)

        toolset_tools = [
            t for t in all_tools
            if not _is_internal_only_tool(getattr(t, "name", ""))
        ]

        log.info(f"Built {len(toolset_tools)} toolset tools for Deep Agent")
        return toolset_tools

    except Exception as e:
        log.error(f"Failed to build toolset tools: {e}", exc_info=True)
        return []


def _is_internal_only_tool(name: str) -> bool:
    """Check if a tool is internal-only and should NOT be passed to Deep Agent
    (Deep Agent has its own planning / filesystem equivalents)."""
    internal_prefixes = ("calculator", "get_current_datetime", "web_search")
    lower = name.lower()
    return any(lower.startswith(p) or lower == p for p in internal_prefixes)


# =============================================================================
# System Prompt Builder
# =============================================================================

def _build_deep_agent_system_prompt(state: ChatState) -> str:
    """
    Build the system prompt prepended before Deep Agent's base prompt.
    Combines PipesHub's agent instructions with citation/tool guidance.
    """
    parts: List[str] = []

    instructions = state.get("instructions")
    if instructions and instructions.strip():
        parts.append(f"## Agent Instructions\n{instructions.strip()}")

    custom_prompt = state.get("system_prompt", "")
    if custom_prompt and custom_prompt.strip() and custom_prompt != "You are an enterprise questions answering expert":
        parts.append(f"## Role\n{custom_prompt.strip()}")

    parts.append(_CITATION_GUIDANCE)

    # Temporal context
    tz = state.get("timezone")
    ct = state.get("current_time")
    if tz or ct:
        time_parts = []
        if ct:
            time_parts.append(f"Current time: {ct}")
        if tz:
            time_parts.append(f"User timezone: {tz}")
        parts.append("## Temporal Context\n" + "\n".join(time_parts))

    # User context
    user_ctx = _format_user_context_for_deep_agent(state)
    if user_ctx:
        parts.append(user_ctx)

    return "\n\n".join(parts)


_CITATION_GUIDANCE = """## Citation & Response Rules

When you use the `knowledge_search` tool and receive results:
1. Cite IMMEDIATELY after each fact: "Revenue grew 29% [R1]."
2. One citation per bracket: [R1][R2] NOT [R1, R2]
3. If no relevant results found, say so — never fabricate.

For API tool results (Slack, Jira, etc.):
- Transform raw data into clean, professional markdown.
- Store technical IDs in your response for follow-up queries.
- If a tool fails, explain the error and suggest alternatives."""


def _format_user_context_for_deep_agent(state: ChatState) -> str:
    """Format user info for inclusion in Deep Agent system prompt."""
    user_info = state.get("user_info", {})
    org_info = state.get("org_info", {})
    user_email = state.get("user_email") or user_info.get("userEmail") or user_info.get("email") or ""
    user_name = (
        user_info.get("fullName")
        or user_info.get("name")
        or user_info.get("displayName")
        or f"{user_info.get('firstName', '')} {user_info.get('lastName', '')}".strip()
    )
    if not user_email and not user_name:
        return ""

    lines = ["## Current User Information", ""]
    if user_name:
        lines.append(f"- **Name**: {user_name}")
    if user_email:
        lines.append(f"- **Email**: {user_email}")
    if org_info.get("accountType"):
        lines.append(f"- **Account Type**: {org_info['accountType']}")
    return "\n".join(lines)


# =============================================================================
# Deep Agent Factory
# =============================================================================

def create_pipeshub_deep_agent(state: ChatState):
    """
    Create a configured Deep Agent instance with PipesHub tools.

    Returns a compiled LangGraph graph (CompiledStateGraph).
    """
    from deepagents import create_deep_agent

    log = state.get("logger", logger)
    llm = state["llm"]

    # Collect tools
    tools: list = []

    has_knowledge = bool(state.get("apps") or state.get("kb") or state.get("agent_knowledge"))
    if has_knowledge:
        tools.append(_build_retrieval_tool(state))
        log.info("Added knowledge_search tool for Deep Agent")

    toolset_tools = _build_toolset_tools(state)
    tools.extend(toolset_tools)

    system_prompt = _build_deep_agent_system_prompt(state)

    log.info(
        f"Creating Deep Agent: model={type(llm).__name__}, "
        f"tools={len(tools)}, has_knowledge={has_knowledge}"
    )

    return create_deep_agent(
        model=llm,
        tools=tools if tools else None,
        system_prompt=system_prompt,
        name="pipeshub-deep-agent",
    )



# =============================================================================
# Message Conversion
# =============================================================================

def _build_messages_for_deep_agent(state: ChatState) -> List[Dict[str, str]]:
    """Convert PipesHub state into the messages list Deep Agent expects."""
    messages: List[Dict[str, str]] = []

    previous = state.get("previous_conversations") or []
    for conv in previous:
        if isinstance(conv, dict):
            if conv.get("role") == "user" or conv.get("type") == "human":
                messages.append({"role": "user", "content": conv.get("content", conv.get("message", ""))})
            elif conv.get("role") == "assistant" or conv.get("type") == "ai":
                messages.append({"role": "assistant", "content": conv.get("content", conv.get("message", ""))})

    query = state.get("query", "")
    if query:
        messages.append({"role": "user", "content": query})

    return messages


# =============================================================================
# Streaming Translation
# =============================================================================

def _translate_deep_agent_stream_event(
    namespace: tuple,
    chunk: Any,
    mode: str,
    writer: Optional[StreamWriter],
    config: Optional[RunnableConfig],
    state: ChatState,
    log: logging.Logger,
    *,
    accumulated_response: List[str],
    tool_results_collector: List[Dict[str, Any]],
) -> None:
    """
    Translate a Deep Agent streaming event into PipesHub SSE events.

    Handles three stream modes: "updates", "messages", "custom".
    """
    is_subagent = any(s.startswith("tools:") for s in namespace) if namespace else False
    source = "subagent" if is_subagent else "main"

    if mode == "updates":
        _handle_update_event(chunk, source, writer, config, state, log, tool_results_collector)

    elif mode == "messages":
        _handle_message_event(chunk, source, writer, config, log, accumulated_response, tool_results_collector)

    elif mode == "custom":
        _handle_custom_event(chunk, source, writer, config, log)


def _handle_update_event(
    chunk: Dict,
    source: str,
    writer: Optional[StreamWriter],
    config: Optional[RunnableConfig],
    state: ChatState,
    log: logging.Logger,
    tool_results_collector: List[Dict[str, Any]],
) -> None:
    """Handle stream_mode='updates' events."""
    if not isinstance(chunk, dict):
        return

    for node_name, data in chunk.items():
        if node_name == "tools":
            for msg in (data.get("messages") or []):
                if getattr(msg, "type", None) == "tool" or isinstance(msg, ToolMessage):
                    tool_name = getattr(msg, "name", "unknown")
                    content = getattr(msg, "content", "")
                    preview = content[:MAX_TOOL_RESULT_PREVIEW_LENGTH] if len(content) > MAX_TOOL_RESULT_PREVIEW_LENGTH else content
                    safe_stream_write(writer, {
                        "event": "tool_result",
                        "data": {"tool": tool_name, "result": preview, "status": "success"}
                    }, config)
                    tool_results_collector.append({
                        "tool_name": tool_name,
                        "status": "success",
                        "result": content,
                    })

                    if "retrieval" in tool_name.lower() or "knowledge" in tool_name.lower():
                        _try_process_retrieval_output(content, state, log)

        elif node_name == "model_request":
            for msg in (data.get("messages") or []):
                for tc in getattr(msg, "tool_calls", []):
                    safe_stream_write(writer, {
                        "event": "tool_call",
                        "data": {
                            "tool": tc.get("name", "unknown"),
                            "args": tc.get("args", {}),
                            "id": tc.get("id", ""),
                        }
                    }, config)


def _handle_message_event(
    chunk: Any,
    source: str,
    writer: Optional[StreamWriter],
    config: Optional[RunnableConfig],
    log: logging.Logger,
    accumulated_response: List[str],
    tool_results_collector: List[Dict[str, Any]],
) -> None:
    """Handle stream_mode='messages' token-level events."""
    if not isinstance(chunk, (list, tuple)) or len(chunk) < 2:
        return

    token, _metadata = chunk[0], chunk[1]

    if getattr(token, "tool_call_chunks", None):
        for tc in token.tool_call_chunks:
            if tc.get("name"):
                safe_stream_write(writer, {
                    "event": "tool_call",
                    "data": {"tool": tc["name"], "args": tc.get("args", "")}
                }, config)

    if getattr(token, "type", None) == "tool":
        tool_name = getattr(token, "name", "unknown")
        content = getattr(token, "content", "")
        safe_stream_write(writer, {
            "event": "tool_result",
            "data": {"tool": tool_name, "result": content[:MAX_TOOL_RESULT_PREVIEW_LENGTH], "status": "success"}
        }, config)
        tool_results_collector.append({"tool_name": tool_name, "status": "success", "result": content})

    if getattr(token, "type", None) == "ai" and getattr(token, "content", "") and not getattr(token, "tool_call_chunks", None):
        text = token.content
        accumulated_response.append(text)
        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {"chunk": text}
        }, config)


def _handle_custom_event(
    chunk: Any,
    source: str,
    writer: Optional[StreamWriter],
    config: Optional[RunnableConfig],
    log: logging.Logger,
) -> None:
    """Handle stream_mode='custom' events (progress / status from Deep Agent tools)."""
    if isinstance(chunk, dict):
        status = chunk.get("status", "processing")
        progress = chunk.get("progress")
        msg = f"[{source}] {status}"
        if progress is not None:
            msg += f" ({progress}%)"
        safe_stream_write(writer, {
            "event": "status",
            "data": {"status": status, "message": msg, "source": source}
        }, config)
    else:
        safe_stream_write(writer, {
            "event": "status",
            "data": {"status": "processing", "message": str(chunk)[:200], "source": source}
        }, config)


def _try_process_retrieval_output(content: str, state: ChatState, log: logging.Logger) -> None:
    """Attempt to parse retrieval output and update state for citation building."""
    try:
        parsed = json.loads(content) if isinstance(content, str) else content
        if isinstance(parsed, dict) and parsed.get("results"):
            results = parsed["results"]
            for item in results:
                vid = item.get("virtual_record_id", "")
                if vid:
                    vr_map = state.get("virtual_record_id_to_result") or {}
                    vr_map[vid] = item
                    state["virtual_record_id_to_result"] = vr_map
    except (json.JSONDecodeError, TypeError, KeyError):
        pass


# =============================================================================
# Deep Agent Graph Node
# =============================================================================

async def deep_agent_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter,
) -> ChatState:
    """
    LangGraph node that runs a Deep Agent for complex multi-step tasks.

    This node:
    1. Creates a Deep Agent with PipesHub tools
    2. Converts PipesHub state into Deep Agent messages
    3. Streams execution, translating events to PipesHub SSE format
    4. Extracts final response and builds completion_data
    """
    start_time = time.perf_counter()
    log = state.get("logger", logger)

    try:
        stream_status(writer, "thinking", "Initializing deep research agent...", config)

        # Create the deep agent
        agent = create_pipeshub_deep_agent(state)
        messages = _build_messages_for_deep_agent(state)

        if not messages:
            state["response"] = "No query provided."
            state["completion_data"] = {
                "answer": state["response"],
                "confidence": "Low",
                "answerMatchType": "Error",
            }
            return state

        stream_status(writer, "planning", "Planning research approach...", config)

        # Stream the deep agent execution
        accumulated_response: List[str] = []
        tool_results: List[Dict[str, Any]] = []

        async for namespace, mode, data in agent.astream(
            {"messages": messages},
            config=config,
            stream_mode=["updates", "messages", "custom"],
            subgraphs=True,
        ):
            _translate_deep_agent_stream_event(
                namespace,
                data,
                mode,
                writer,
                config,
                state,
                log,
                accumulated_response=accumulated_response,
                tool_results_collector=tool_results,
            )

        # Extract final response
        response_text = "".join(accumulated_response)
        if not response_text:
            try:
                final_state = await agent.ainvoke({"messages": messages}, config=config)
                final_messages = final_state.get("messages", [])
                for msg in reversed(final_messages):
                    if isinstance(msg, AIMessage) and msg.content and not getattr(msg, "tool_calls", None):
                        response_text = msg.content
                        break
            except Exception as e:
                log.warning(f"Fallback ainvoke also failed: {e}")

        if not response_text:
            response_text = "I completed the deep research task but couldn't generate a final response."

        # Build completion data
        final_results = state.get("final_results", [])
        virtual_record_map = state.get("virtual_record_id_to_result", {})
        has_retrieval = bool(final_results)
        api_tools = [r for r in tool_results if "retrieval" not in r.get("tool_name", "").lower() and "knowledge" not in r.get("tool_name", "").lower()]

        citations, block_numbers, reference_data = _extract_deep_agent_citations(
            response_text, final_results, virtual_record_map, tool_results, log
        )

        if has_retrieval and api_tools:
            answer_match_type = "Derived From Blocks"
        elif has_retrieval:
            answer_match_type = "Derived From Blocks"
        elif api_tools:
            answer_match_type = "Derived From Tool Execution"
        else:
            answer_match_type = "Direct Answer"

        completion_data: Dict[str, Any] = {
            "answer": response_text,
            "citations": citations,
            "confidence": "High",
            "answerMatchType": answer_match_type,
        }
        if block_numbers:
            completion_data["blockNumbers"] = block_numbers
        if reference_data:
            completion_data["referenceData"] = reference_data

        # Stream the completion event
        safe_stream_write(writer, {
            "event": "complete",
            "data": completion_data,
        }, config)

        state["response"] = response_text
        state["completion_data"] = completion_data
        state["tool_results"] = tool_results
        state["all_tool_results"] = tool_results

        duration_ms = (time.perf_counter() - start_time) * 1000
        log.info(
            f"Deep Agent completed: {duration_ms:.0f}ms, "
            f"{len(tool_results)} tool calls, "
            f"{len(final_results)} retrieval results"
        )

    except ImportError as e:
        log.error(f"Deep Agent dependencies not available: {e}")
        state["response"] = "Deep research agent is not available. Please install the 'deepagents' package."
        state["completion_data"] = {
            "answer": state["response"],
            "confidence": "Low",
            "answerMatchType": "Error",
        }
        safe_stream_write(writer, {
            "event": "error",
            "data": {"message": state["response"]},
        }, config)

    except Exception as e:
        log.error(f"Deep Agent error: {e}", exc_info=True)
        state["response"] = f"I encountered an error during deep research: {e}"
        state["completion_data"] = {
            "answer": state["response"],
            "confidence": "Low",
            "answerMatchType": "Error",
        }
        safe_stream_write(writer, {
            "event": "error",
            "data": {"message": str(e)},
        }, config)

    return state


# =============================================================================
# Citation Extraction
# =============================================================================

def _extract_deep_agent_citations(
    response: str,
    final_results: List[Dict],
    virtual_record_map: Dict[str, Dict[str, Any]],
    tool_results: List[Dict[str, Any]],
    log: logging.Logger,
) -> Tuple[List[Dict], List[str], List[Dict]]:
    """
    Extract citations, block numbers, and reference data from the deep agent response.

    Returns:
        (citations, block_numbers, reference_data)
    """
    citations: List[Dict] = []
    block_numbers: List[str] = []
    reference_data: List[Dict] = []

    # Extract block references like [R1-1], [R2-3] from response text
    matches = BLOCK_PATTERN.findall(response)
    for match in matches:
        block_num = f"R{match[0]}-{match[1]}"
        if block_num not in block_numbers:
            block_numbers.append(block_num)

    # Also handle simpler [R1], [R2] references
    simple_pattern = re.compile(r"\[R(\d+)\]")
    simple_matches = simple_pattern.findall(response)
    for match in simple_matches:
        block_num = f"R{match}"
        if block_num not in block_numbers:
            block_numbers.append(block_num)

    # Build citations from retrieval results
    if final_results:
        for idx, result in enumerate(final_results):
            metadata = result.get("metadata", {}) if isinstance(result, dict) else {}
            content = result.get("page_content", "") if isinstance(result, dict) else ""
            virtual_id = metadata.get("virtual_record_id", "")
            source = metadata.get("source", metadata.get("title", f"Document-{idx+1}"))
            block_num = f"R{idx+1}"

            citations.append({
                "source": source,
                "type": "retrieval",
                "content": content[:200],
                "virtual_id": virtual_id,
                "block_id": block_num,
            })

    # Extract reference data from API tool results
    api_results = [
        r for r in tool_results
        if r.get("status") == "success"
        and "retrieval" not in r.get("tool_name", "").lower()
        and "knowledge" not in r.get("tool_name", "").lower()
    ]
    for result in api_results:
        try:
            content = result.get("result", "")
            if isinstance(content, str):
                try:
                    parsed = json.loads(content)
                except (json.JSONDecodeError, ValueError):
                    parsed = content
            else:
                parsed = content

            if isinstance(parsed, dict):
                ref = {"tool": result.get("tool_name", ""), "data": _extract_ids_from_dict(parsed)}
                if ref["data"]:
                    reference_data.append(ref)
        except Exception:
            pass

    return citations, block_numbers, reference_data


def _extract_ids_from_dict(data: Dict[str, Any], max_depth: int = 2) -> Dict[str, Any]:
    """Extract ID-like fields from a dict for reference data."""
    if max_depth <= 0:
        return {}
    ids: Dict[str, Any] = {}
    for key, value in data.items():
        lower_key = key.lower()
        if any(kw in lower_key for kw in ("id", "key", "url", "link", "name", "title")):
            if isinstance(value, (str, int)):
                ids[key] = value
        elif isinstance(value, dict) and max_depth > 1:
            nested = _extract_ids_from_dict(value, max_depth - 1)
            if nested:
                ids[key] = nested
    return ids
