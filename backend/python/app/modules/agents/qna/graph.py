from langgraph.graph import END, StateGraph

from app.modules.agents.qna.chat_state import ChatState
from app.modules.agents.qna.nodes import (
    agent_node,
    analyze_query_node,
    check_for_error,
    conditional_retrieve_node,
    final_response_node,
    get_user_info_node,
    prepare_clean_prompt_node,
    tool_execution_node,
)


def should_continue_with_limit(state: ChatState) -> str:
    """Simple routing with safety limit - pure LLM autonomy"""
    # Get tool execution count
    all_tool_results = state.get("all_tool_results", [])
    tool_call_count = len(all_tool_results)

    # Safety limit to prevent infinite loops - generous for enterprise workflows
    max_iterations = 20  # Increased to support complex multi-tool enterprise workflows

    # Simple decision based on LLM's choice
    has_pending_calls = state.get("pending_tool_calls", False)

    # Log for debugging
    logger = state.get("logger")
    if logger:
        logger.debug(f"Tool routing: pending={has_pending_calls}, count={tool_call_count}/{max_iterations}")

        # Show recent tool usage for complex workflow tracking
        if all_tool_results and len(all_tool_results) > 0:
            recent_tools = [result.get("tool_name", "unknown") for result in all_tool_results[-5:]]
            logger.debug(f"Recent tool chain: {' → '.join(recent_tools)}")

    # Simple routing logic - let LLM drive everything
    if has_pending_calls and tool_call_count < max_iterations:
        return "execute_tools"
    else:
        if tool_call_count >= max_iterations and logger:
            logger.warning(f"Reached maximum tool iterations ({max_iterations}), proceeding to final response")
            logger.info("This prevents infinite loops while allowing complex enterprise workflows")
        return "final"


def llm_qna_graph() -> StateGraph:
    """Create a pure LLM-driven QnA graph with complete tool autonomy and no ToolExecutor dependency"""

    workflow = StateGraph(ChatState)

    # Add nodes - each focused on specific functionality, LLM makes all tool decisions
    workflow.add_node("analyze", analyze_query_node)                    # Simple query analysis
    workflow.add_node("conditional_retrieve", conditional_retrieve_node) # Data retrieval when needed
    workflow.add_node("get_user", get_user_info_node)                   # User context
    workflow.add_node("prepare_prompt", prepare_clean_prompt_node)      # Present ALL tools to LLM
    workflow.add_node("agent", agent_node)                       # LLM decides everything autonomously
    workflow.add_node("execute_tools", tool_execution_node)       # Execute ANY tool directly (no executor)
    workflow.add_node("final", final_response_node)               # Final response

    # Set entry point
    workflow.set_entry_point("analyze")

    # Build flow - simple, linear, error-handled
    workflow.add_conditional_edges(
        "analyze",
        check_for_error,
        {
            "continue": "conditional_retrieve",
            "error": END
        }
    )

    workflow.add_conditional_edges(
        "conditional_retrieve",
        check_for_error,
        {
            "continue": "get_user",
            "error": END
        }
    )

    # Linear flow to prompt preparation
    workflow.add_edge("get_user", "prepare_prompt")

    workflow.add_conditional_edges(
        "prepare_prompt",
        check_for_error,
        {
            "continue": "agent",
            "error": END
        }
    )

    # LLM decides autonomously - supports unlimited tool combinations and workflows
    # Examples: Slack → JIRA → Confluence → Email → Calendar (all LLM-driven)
    workflow.add_conditional_edges(
        "agent",
        should_continue_with_limit,
        {
            "execute_tools": "execute_tools",
            "final": "final"
        }
    )

    # Critical: After tool execution, return to agent for potential additional tool calls
    # This enables natural multi-step workflows like:
    # 1. Search Slack for bug reports
    # 2. Create JIRA ticket
    # 3. Update Confluence documentation
    # 4. Send email notification
    # 5. Schedule follow-up meeting
    workflow.add_edge("execute_tools", "agent")

    # End the workflow
    workflow.add_edge("final", END)

    return workflow.compile()


# Export the pure LLM-driven graph with direct tool execution
qna_graph = llm_qna_graph()
