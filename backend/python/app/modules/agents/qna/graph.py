from langgraph.graph import END, StateGraph

from app.modules.agents.qna.chat_state import ChatState
from app.modules.agents.qna.nodes import (  # Clean nodes
    analyze_query_node,
    conditional_retrieve_node,
    get_user_info_node,
    prepare_clean_prompt_node,
    clean_agent_node,
    clean_tool_execution_node,
    clean_final_response_node,
    should_continue,
    check_for_error,
)


def should_continue_with_limit(state: ChatState) -> str:
    """Route based on pending tool calls with a safety limit"""
    # Check if there are pending tool calls
    has_pending_calls = state.get("pending_tool_calls", False)
    
    # Get current tool call count with proper null handling
    tool_results = state.get("tool_results")
    tool_call_count = len(tool_results) if tool_results is not None else 0
    
    # Safety limits
    max_tool_iterations = 5
    
    # Log for debugging
    logger = state.get("logger")
    if logger:
        logger.debug(f"Routing decision: pending_calls={has_pending_calls}, tool_count={tool_call_count}")
    
    # Continue with tools if we have pending calls and haven't hit the limit
    if has_pending_calls and tool_call_count < max_tool_iterations:
        return "execute_tools"
    else:
        if tool_call_count >= max_tool_iterations and logger:
            logger.warning(f"Hit tool call limit ({max_tool_iterations}), proceeding to final response")
        return "final"


def create_clean_qna_graph() -> StateGraph:
    """Create a clean QnA graph where LLM makes natural tool decisions with multi-tool support"""

    workflow = StateGraph(ChatState)

    # Add clean nodes (minimal forced logic)
    workflow.add_node("analyze", analyze_query_node)                    # Simple analysis for retrieval only
    workflow.add_node("conditional_retrieve", conditional_retrieve_node) # Only retrieve if needed
    workflow.add_node("get_user", get_user_info_node)                   # Get user info
    workflow.add_node("prepare_prompt", prepare_clean_prompt_node)      # Clean prompt preparation
    workflow.add_node("agent", clean_agent_node)                       # Pure LLM tool decision making
    workflow.add_node("execute_tools", clean_tool_execution_node)       # Execute LLM-chosen tools
    workflow.add_node("final", clean_final_response_node)               # Clean final response

    # Set entry point
    workflow.set_entry_point("analyze")

    # Build the clean flow
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

    # Always continue to prompt preparation after user info
    workflow.add_edge("get_user", "prepare_prompt")

    workflow.add_conditional_edges(
        "prepare_prompt",
        check_for_error,
        {
            "continue": "agent", 
            "error": END
        }
    )

    # Agent decides naturally whether to use tools or provide final response
    workflow.add_conditional_edges(
        "agent",
        should_continue_with_limit,  # Use the safer version with limits
        {
            "execute_tools": "execute_tools",
            "final": "final"
        }
    )

    # CRITICAL FIX: After tool execution, go back to agent to potentially call more tools
    workflow.add_edge("execute_tools", "agent")

    # Final response ends the workflow
    workflow.add_edge("final", END)

    return workflow.compile()


# Export the clean graph
qna_graph = create_clean_qna_graph()