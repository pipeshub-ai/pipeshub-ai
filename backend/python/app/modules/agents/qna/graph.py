"""
Agent Graph - LLM-Driven Planner Architecture with Reflection

This module defines the agent execution graph using LangGraph.
The architecture is fully LLM-driven with intelligent error recovery.

Architecture:
    ┌───────────────────────────────────────────────────────────────────────┐
    │                                                                       │
    │   Entry ──▶ Planner ──▶ Execute ──▶ Reflect ──▶ Respond ──▶ End      │
    │              (LLM)      (Parallel)    (Fast)     (LLM)                │
    │                │                         │         ▲                  │
    │                │                         │         │                  │
    │                │                    retry_with_fix │                  │
    │                │                         │         │                  │
    │                │                         ▼         │                  │
    │                │                   PrepareRetry ───┘                  │
    │                │                         │                            │
    │                │                         │ (back to planner)          │
    │                └─────────────────────────┴────────────────────────────┘
    │                                                                       │
    └───────────────────────────────────────────────────────────────────────┘

Flow:
1. **Planner Node** (LLM): Analyzes query and creates execution plan
2. **Execute Node**: Runs all planned tools in parallel
3. **Reflect Node**: Analyzes results, decides next action (fast-path or LLM)
4. **PrepareRetry Node**: Sets up retry context (if reflection says retry)
5. **Respond Node** (LLM): Generates final response with citations

Reflection Decisions:
- respond_success: Tools worked, generate response
- respond_error: Unrecoverable error, give friendly message
- respond_clarify: Need user input, ask clarifying question
- retry_with_fix: Fixable error, retry with adjusted approach (max 1 retry)

Performance Targets:
- Simple queries (no tools): ~2-3s
- Success (tools work): ~6-8s
- Retry (one fix needed): ~10-14s
- Error (unrecoverable): ~6-8s
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langgraph.graph import END, StateGraph

from app.modules.agents.qna.chat_state import ChatState
from app.modules.agents.qna.nodes import (
    execute_node,
    planner_node,
    prepare_continue_node,
    prepare_retry_node,
    reflect_node,
    respond_node,
    route_after_reflect,
    should_execute_tools,
)

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger(__name__)


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
    # Create workflow
    workflow = StateGraph(ChatState)

    # Add nodes
    workflow.add_node("planner", planner_node)
    workflow.add_node("execute", execute_node)
    workflow.add_node("reflect", reflect_node)
    workflow.add_node("prepare_retry", prepare_retry_node)
    workflow.add_node("prepare_continue", prepare_continue_node)
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

    # From execute: go to reflect for analysis
    workflow.add_edge("execute", "reflect")

    # From reflect: either retry, continue, or respond
    workflow.add_conditional_edges(
        "reflect",
        route_after_reflect,
        {
            "prepare_retry": "prepare_retry",
            "prepare_continue": "prepare_continue",
            "respond": "respond"
        }
    )

    # From prepare_retry: go back to planner
    workflow.add_edge("prepare_retry", "planner")

    # From prepare_continue: go back to planner (for multi-step tasks)
    workflow.add_edge("prepare_continue", "planner")

    # From respond: end the graph
    workflow.add_edge("respond", END)

    # Compile and return
    return workflow.compile()


# Create the compiled graph instance
agent_graph = create_agent_graph()


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    "agent_graph",
    "create_agent_graph",
]
