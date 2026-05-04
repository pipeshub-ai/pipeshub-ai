"""
Deep Agent Graph - Orchestrator + Critic + Sub-Agent Architecture
 
Graph topology:
 
    Entry → Orchestrator ──→ route_after_orchestrator ──┬──(critic)──→ Critic ──→ route_after_critic ──┬──(dispatch)──→ Execute ──→ Aggregator ──→ Respond → End
                ↑                                        │                                              │
                │                                        └──(bypass_critic/direct)─────────────────────┘
                │                                                                                       │
                └──(retry/continue from aggregator, or revise from critic) ─────────────────────────── ┘
 

"""
 
from __future__ import annotations
 
from typing import TYPE_CHECKING, Literal
 
from langgraph.graph import END, StateGraph
 
from app.modules.agents.deep.aggregator import aggregator_node, route_after_evaluation
from app.modules.agents.deep.orchestrator import orchestrator_node, should_dispatch
from app.modules.agents.deep.orchestrator_critic import critic_node, route_after_critic
from app.modules.agents.deep.respond import deep_respond_node
from app.modules.agents.deep.state import DeepAgentState
from app.modules.agents.deep.sub_agent import execute_sub_agents_node
 
if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph
 
 
def route_after_orchestrator(
    state: DeepAgentState,
) -> Literal["critic", "dispatch", "respond"]:
    """
    Route after orchestrator node.
 
    First run (critic_done=False): always go to critic.
    Subsequent runs (critic_done=True): bypass critic, go straight to
    dispatch or respond — the critic only runs once per user request.
    """
    if state.get("error"):
        return "respond"
 
    if not state.get("critic_done", False):
        execution_plan = state.get("execution_plan", {}) or {}
        if execution_plan.get("can_answer_directly"):
            return "respond"
        # First orchestrator run — critic has not evaluated yet.
        return "critic"

    # Critic already ran (either approved or triggered one revision).
    # Go straight to execution — no second critic evaluation.
    return should_dispatch(state)  # "dispatch" or "respond"
 
 
def create_deep_agent_graph() -> "CompiledStateGraph":
    """
    Create the deep agent graph with orchestrator + critic + sub-agents.
 
    Nodes:
        orchestrator      : Decomposes query into sub-tasks.
        critic            : Evaluates the plan — runs EXACTLY ONCE.
        execute_sub_agents: Runs sub-agents with isolated contexts.
        aggregator        : Evaluates results, decides next action.
        respond           : Generates final response.
 
    Routing:
        orchestrator → route_after_orchestrator:
            "critic"    → critic            (first run only)
            "dispatch"  → execute_sub_agents (subsequent runs, has tasks)
            "respond"   → respond            (subsequent runs, direct answer)
 
        critic → route_after_critic:
            "dispatch"     → execute_sub_agents (approved, has tasks)
            "respond"      → respond            (approved, direct answer / error)
            "orchestrator" → orchestrator       (revise — one re-plan, then bypassed)
 
        execute_sub_agents → aggregator
        aggregator → route_after_evaluation:
            "respond"  → respond
            "retry"    → orchestrator   (critic bypassed on this pass)
            "continue" → orchestrator   (critic bypassed on this pass)
        respond → END
    """
    workflow = StateGraph(DeepAgentState)
 
    workflow.add_node("orchestrator", orchestrator_node)
    workflow.add_node("critic", critic_node)
    workflow.add_node("execute_sub_agents", execute_sub_agents_node)
    workflow.add_node("aggregator", aggregator_node)
    workflow.add_node("respond", deep_respond_node)
 
    workflow.set_entry_point("orchestrator")
 
    # Orchestrator → critic (first run) or bypass (subsequent runs)
    workflow.add_conditional_edges(
        "orchestrator",
        route_after_orchestrator,
        {
            "critic": "critic",
            "dispatch": "execute_sub_agents",
            "respond": "respond",
        },
    )
 
    # Critic → dispatch | orchestrator (one re-plan) | respond
    workflow.add_conditional_edges(
        "critic",
        route_after_critic,
        {
            "dispatch": "execute_sub_agents",
            "orchestrator": "orchestrator",
            "respond": "respond",
        },
    )
 
    workflow.add_edge("execute_sub_agents", "aggregator")
 
    workflow.add_conditional_edges(
        "aggregator",
        route_after_evaluation,
        {
            "respond": "respond",
            "retry": "orchestrator",
            "continue": "orchestrator",
        },
    )
 
    workflow.add_edge("respond", END)
 
    return workflow.compile()
 
 
deep_agent_graph = create_deep_agent_graph()
 
__all__ = ["create_deep_agent_graph", "deep_agent_graph"]