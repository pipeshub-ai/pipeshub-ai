"""
Deep Agent Graph - Orchestrator + Sub-Agent Architecture

Wires together: orchestrator → execute_sub_agents → aggregator → respond
with conditional routing for retry/continue loops.

Architecture:
    Entry → Orchestrator ──→ Dispatch ──→ Execute Sub-Agents ──→ Aggregator ──→ Respond → End
                  │                                                  │   │
                  └──── (direct answer) ─────────────────────────────┘   │
                                                                        │
                  ┌──── (retry/continue) ───────────────────────────────┘
                  ↓
            Orchestrator (re-plan with previous results)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.graph import END, StateGraph

from app.modules.agents.deep.aggregator import aggregator_node, route_after_evaluation
from app.modules.agents.deep.orchestrator import orchestrator_node, should_dispatch
from app.modules.agents.deep.respond import deep_respond_node
from app.modules.agents.deep.state import DeepAgentState
from app.modules.agents.deep.sub_agent import execute_sub_agents_node

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph


def create_deep_agent_graph() -> "CompiledStateGraph":
    """
    Create the deep agent graph with orchestrator + sub-agents.

    Nodes:
        orchestrator: Decomposes query into sub-tasks
        execute_sub_agents: Runs sub-agents with isolated contexts
        aggregator: Evaluates results, decides next action
        respond: Generates final response from domain summaries (dedicated deep agent node)

    Edges:
        orchestrator → should_dispatch → dispatch | respond
        dispatch (execute_sub_agents) → aggregator
        aggregator → route_after_evaluation → respond | retry | continue
        retry/continue → orchestrator (re-plan)
        respond → END

    Returns:
        Compiled StateGraph ready for execution
    """
    workflow = StateGraph(DeepAgentState)

    # Add nodes
    workflow.add_node("orchestrator", orchestrator_node)
    workflow.add_node("execute_sub_agents", execute_sub_agents_node)
    workflow.add_node("aggregator", aggregator_node)
    workflow.add_node("respond", deep_respond_node)

    # Entry point
    workflow.set_entry_point("orchestrator")

    # Orchestrator → dispatch or respond directly
    workflow.add_conditional_edges(
        "orchestrator",
        should_dispatch,
        {
            "dispatch": "execute_sub_agents",
            "respond": "respond",
        },
    )

    # Execute sub-agents → aggregator
    workflow.add_edge("execute_sub_agents", "aggregator")

    # Aggregator → respond, retry, or continue
    workflow.add_conditional_edges(
        "aggregator",
        route_after_evaluation,
        {
            "respond": "respond",
            "retry": "orchestrator",
            "continue": "orchestrator",
        },
    )

    # Respond → END
    workflow.add_edge("respond", END)

    return workflow.compile()


# Create the compiled graph instance
deep_agent_graph = create_deep_agent_graph()


__all__ = [
    "create_deep_agent_graph",
    "deep_agent_graph",
]
