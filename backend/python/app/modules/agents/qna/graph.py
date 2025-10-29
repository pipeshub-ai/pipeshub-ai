"""
Planning-Based Agent Graph
Supports complex multi-step workflows with planning, execution, and adaptation
"""

from langgraph.graph import END, StateGraph

from app.modules.agents.qna.chat_state import ChatState
from app.modules.agents.qna.nodes import (
    agent_node,
    analyze_query_node,
    check_for_error,
    conditional_retrieve_node,
    final_response_node,
    get_user_info_node,
    prepare_agent_prompt_node,
    tool_execution_node,
)

# Constants
LAST_N_TOOLS = 5
MAX_TOOL_ITERATIONS = 30
PING_PONG_PATTERN_THRESHOLD = 2


def should_continue_with_planning(state: ChatState) -> str:
    """
    Enhanced routing with planning awareness and loop detection
    """
    all_tool_results = state.get("all_tool_results", [])
    tool_call_count = len(all_tool_results)
    max_iterations = MAX_TOOL_ITERATIONS

    has_pending_calls = state.get("pending_tool_calls", False)
    is_complex = state.get("requires_planning", False)

    logger = state.get("logger")
    if logger:
        status = "complex workflow" if is_complex else "simple query"
        logger.debug(f"Routing decision ({status}): pending={has_pending_calls}, iteration={tool_call_count}/{max_iterations}")

        # Enhanced tracking for planning workflows
        if all_tool_results and len(all_tool_results) > 0:
            recent_tools = [result.get("tool_name", "unknown") for result in all_tool_results[-LAST_N_TOOLS:]]
            logger.debug(f"Workflow chain: {' → '.join(recent_tools)}")

            # Detect stuck loops
            if len(all_tool_results) >= LAST_N_TOOLS:
                last_n_tools = [result.get("tool_name", "unknown") for result in all_tool_results[-LAST_N_TOOLS:]]
                unique_tools = set(last_n_tools)

                if len(unique_tools) == 1:
                    logger.warning(f"⚠️ Loop detected: {last_n_tools[0]} called {LAST_N_TOOLS} times in a row")
                    logger.warning("Forcing termination to prevent infinite loop")
                    return "final"

                elif len(unique_tools) == PING_PONG_PATTERN_THRESHOLD:
                    # Check for A→B→A→B pattern
                    pattern = "→".join(last_n_tools)
                    logger.warning(f"⚠️ Potential ping-pong detected: {pattern}")
                    logger.warning("Forcing termination after 2 more iterations")

                    # Allow 2 more iterations then force stop
                    if tool_call_count >= LAST_N_TOOLS + 2:
                        return "final"

            # Log workflow progress for complex queries
            if is_complex and tool_call_count > 0:
                logger.info(f"📊 Workflow progress: {tool_call_count} steps completed")

    # Make routing decision
    if has_pending_calls and tool_call_count < max_iterations:
        return "execute_tools"
    else:
        if tool_call_count >= max_iterations and logger:
            logger.warning(f"⚠️ Maximum iterations reached ({max_iterations})")
            if is_complex:
                logger.info("Complex workflow exceeded iteration limit - providing best-effort response")
        return "final"


def create_agent_graph() -> StateGraph:
    """
    Create an advanced planning-based agent graph that supports:
    - Multi-step workflow planning
    - Dynamic adaptation based on results
    - Complex query decomposition
    - Tool chaining and orchestration
    - Both conversational and structured outputs

    The agent uses PLAN → EXECUTE → ADAPT framework for optimal results.
    """

    workflow = StateGraph(ChatState)

    # =========================================================================
    # NODE DEFINITIONS
    # =========================================================================

    # Phase 1: Analysis & Planning
    workflow.add_node("analyze", analyze_query_node)
    # Analyzes query complexity, detects follow-ups, determines retrieval needs

    # Phase 2: Knowledge Retrieval
    workflow.add_node("retrieve", conditional_retrieve_node)
    # Smart retrieval with complexity-based limit adjustment

    # Phase 3: Context Building
    workflow.add_node("get_user", get_user_info_node)
    # Fetches user/org context for personalization

    # Phase 4: Planning Preparation
    workflow.add_node("prepare", prepare_agent_prompt_node)
    # Builds agent-enhanced prompt with workflow guidance

    # Phase 5: Agent Planning & Reasoning
    workflow.add_node("agent", agent_node)
    # Core planning agent that creates execution plans and adapts

    # Phase 6: Tool Execution
    workflow.add_node("execute_tools", tool_execution_node)
    # Executes tools with planning context and iteration tracking

    # Phase 7: Final Response
    workflow.add_node("final", final_response_node)
    # Generates final response with workflow summary

    # =========================================================================
    # WORKFLOW EDGES
    # =========================================================================

    # Entry point
    workflow.set_entry_point("analyze")

    # Analysis → Retrieval (with error handling)
    workflow.add_conditional_edges(
        "analyze",
        check_for_error,
        {
            "continue": "retrieve",
            "error": END
        }
    )

    # Retrieval → User Context (with error handling)
    workflow.add_conditional_edges(
        "retrieve",
        check_for_error,
        {
            "continue": "get_user",
            "error": END
        }
    )

    # User Context → Planning Preparation
    workflow.add_edge("get_user", "prepare")

    # Preparation → Agent (with error handling)
    workflow.add_conditional_edges(
        "prepare",
        check_for_error,
        {
            "continue": "agent",
            "error": END
        }
    )

    # CRITICAL: Agent Decision Point
    # This is where planning happens. The agent:
    # 1. Creates or updates execution plan
    # 2. Decides: use tools OR provide final answer
    # 3. Adapts based on previous tool results
    workflow.add_conditional_edges(
        "agent",
        should_continue_with_planning,
        {
            "execute_tools": "execute_tools",
            "final": "final"
        }
    )

    # CRITICAL: Tool Execution Loop
    # After tools execute, return to agent for:
    # - Result evaluation
    # - Plan adaptation
    # - Decision on next steps
    # This enables complex multi-step workflows like:
    #   Search Slack → Extract info → Create JIRA → Update Confluence → Email team
    #   Get calendar → Find relevant docs for each meeting → Summarize → Email
    #   Check metrics → Compare to goals → Search best practices → Generate report
    workflow.add_edge("execute_tools", "agent")

    # Final response and end
    workflow.add_edge("final", END)

    return workflow.compile()


# =========================================================================
# EXPORT
# =========================================================================

agent_graph = create_agent_graph()
