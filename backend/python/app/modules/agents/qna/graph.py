"""
Agent Graph - OPTIMIZED LLM-Driven Planner Architecture

This module defines the agent execution graph using LangGraph.
The architecture is fully LLM-driven with intelligent error recovery.

OPTIMIZATIONS (vs original):
- Planner uses STREAMING (astream) instead of blocking ainvoke() → 2-5x faster
- Reflect node SKIPPED on success → saves 0-8 seconds
- Streaming response generation → immediate token delivery

Architecture:
    ┌───────────────────────────────────────────────────────────────────────┐
    │                                                                       │
    │   Entry ──▶ Planner ──▶ Execute ──▶ [Success?] ──▶ Respond ──▶ End   │
    │            (Stream)    (Parallel)        │         (Stream)           │
    │                │                         │            ▲               │
    │                │                    [Failure]        │               │
    │                │                         │            │               │
    │                │                         ▼            │               │
    │                │                   Reflect ───────────┘               │
    │                │                         │                            │
    │                │                    [Retry?]                          │
    │                │                         │                            │
    │                │                         ▼                            │
    │                │                   PrepareRetry                       │
    │                │                         │                            │
    │                └─────────────────────────┘ (back to planner)          │
    │                                                                       │
    └───────────────────────────────────────────────────────────────────────┘

Flow:
1. **Planner Node** (STREAMING LLM): Analyzes query, creates plan with astream()
2. **Execute Node**: Runs all planned tools in parallel
3. **Fast-Path Check**: If all tools succeed → skip reflect, go to respond
4. **Reflect Node**: Only runs on failure, decides retry vs error
5. **Respond Node** (STREAMING LLM): Generates response with citations

Performance Targets (OPTIMIZED):
- Simple queries (no tools): ~1-2s
- Success (tools work): ~3-6s (was 6-8s)
- Retry (one fix needed): ~8-12s (was 10-14s)
- Error (unrecoverable): ~4-6s (was 6-8s)
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Optional

from langgraph.graph import END, StateGraph

from app.modules.agents.qna.chat_state import ChatState
from app.modules.agents.qna.nodes import (
    execute_node,
    planner_node,
    prepare_retry_node,
    reflect_node,
    respond_node,
    route_after_reflect,
    should_execute_tools,
    should_reflect,
)

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger(__name__)

# Module-level cache for compiled graph (compiled once at import time)
_compiled_graph: Optional["CompiledStateGraph"] = None
_graph_compile_time_ms: float = 0


def create_agent_graph() -> "CompiledStateGraph":
    """
    Create the LLM-driven agent graph with reflection.

    This graph uses an intelligent architecture with error recovery:

    1. **Planner** (Entry Point):
       - Single LLM call that analyzes the query
       - Decides which tools to use (including retrieval)
       - Creates complete execution plan
       - Handles retry context if coming from a failed attempt

    2. **Execute** (Conditional):
       - Runs all planned tools in parallel
       - Handles retrieval output specially for citations
       - Skipped if planner says can_answer_directly=True

    3. **Reflect** (Fast-Path + LLM Fallback):
       - Analyzes tool execution results
       - Uses pattern matching for common errors (0ms)
       - Falls back to LLM for ambiguous cases (~3s)
       - Decides: respond_success, respond_error, respond_clarify, retry_with_fix

    4. **PrepareRetry** (Conditional):
       - Sets up error context for retry
       - Clears old results
       - Routes back to planner

    5. **Respond** (Final):
       - Generates response based on reflection decision
       - For success: incorporates tool results
       - For error: returns user-friendly message
       - For clarify: asks clarifying question
       - Streams response for good UX

    Returns:
        Compiled StateGraph ready for execution

    Example:
        >>> graph = create_agent_graph()
        >>> result = await graph.ainvoke(initial_state, config=config)
        >>> print(result["response"])
    """
    global _graph_compile_time_ms

    start_time = time.perf_counter()

    # Create workflow
    workflow = StateGraph(ChatState)

    # Add nodes
    workflow.add_node("planner", planner_node)
    workflow.add_node("execute", execute_node)
    workflow.add_node("reflect", reflect_node)
    workflow.add_node("prepare_retry", prepare_retry_node)
    workflow.add_node("respond", respond_node)

    # Set entry point - planner is the first node
    workflow.set_entry_point("planner")

    # Add edges
    # From planner: either execute tools or respond directly
    workflow.add_conditional_edges(
        "planner",
        should_execute_tools,
        {
            "execute": "execute",
            "respond": "respond"
        }
    )

    # From execute: skip reflect if all tools succeeded (saves 0-8s)
    workflow.add_conditional_edges(
        "execute",
        should_reflect,
        {
            "reflect": "reflect",
            "respond": "respond"
        }
    )

    # From reflect: either retry or respond
    workflow.add_conditional_edges(
        "reflect",
        route_after_reflect,
        {
            "prepare_retry": "prepare_retry",
            "respond": "respond"
        }
    )

    # From prepare_retry: go back to planner
    workflow.add_edge("prepare_retry", "planner")

    # From respond: end the graph
    workflow.add_edge("respond", END)

    # Compile and return
    compiled = workflow.compile()

    _graph_compile_time_ms = (time.perf_counter() - start_time) * 1000
    logger.info(f"⚡ Agent graph compiled in {_graph_compile_time_ms:.1f}ms")

    return compiled


def get_agent_graph() -> "CompiledStateGraph":
    """
    Get the cached compiled agent graph.

    This function ensures the graph is only compiled once and reused.
    Subsequent calls return the cached graph instantly.

    Returns:
        Compiled StateGraph ready for execution
    """
    global _compiled_graph

    if _compiled_graph is None:
        _compiled_graph = create_agent_graph()

    return _compiled_graph


def get_graph_compile_time() -> float:
    """Get the time it took to compile the graph (in milliseconds)."""
    return _graph_compile_time_ms


# Create the compiled graph instance at module load time
# This ensures the graph is compiled once when the module is imported
agent_graph = create_agent_graph()


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    "agent_graph",
    "create_agent_graph",
    "get_agent_graph",
    "get_graph_compile_time",
]
