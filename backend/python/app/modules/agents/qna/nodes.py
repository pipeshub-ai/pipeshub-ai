# Clean Intelligent Agent System - LLM-Driven Tool Selection
import asyncio
from datetime import datetime
from typing import Literal

from langgraph.types import StreamWriter
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.config.constants.arangodb import AccountType, CollectionNames
from app.modules.agents.qna.chat_state import ChatState
from app.modules.qna.prompt_templates import qna_prompt
from app.utils.citations import process_citations


# 1. Query Analysis Node - Simple analysis for conditional retrieval only
async def analyze_query_node(
    state: ChatState,
    writer: StreamWriter
) -> ChatState:
    """Simple analysis to determine if internal data retrieval is needed"""
    try:
        logger = state["logger"]
        
        writer({"event": "status", "data": {"status": "analyzing", "message": "Analyzing query requirements..."}})
        
        # Simple logic: check for explicit filters or keywords that suggest internal data need
        has_kb_filter = bool(state.get("filters", {}).get("kb"))
        has_app_filter = bool(state.get("filters", {}).get("apps"))
        
        # Keywords that suggest internal data need
        internal_keywords = [
            "our", "my", "company", "organization", "internal", "knowledge base", 
            "documents", "files", "emails", "data", "records"
        ]
        
        query_lower = state["query"].lower()
        needs_internal_data = (
            has_kb_filter or 
            has_app_filter or 
            any(keyword in query_lower for keyword in internal_keywords)
        )
        
        state["query_analysis"] = {
            "needs_internal_data": needs_internal_data,
            "reasoning": f"KB filter: {has_kb_filter}, App filter: {has_app_filter}, Internal keywords detected: {any(keyword in query_lower for keyword in internal_keywords)}"
        }
        
        logger.info(f"Query analysis: needs_internal_data = {needs_internal_data}")
        return state
        
    except Exception as e:
        logger.error(f"Error in query analysis node: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state


# 2. Conditional Retrieval Node - Only retrieves if analysis suggests it's needed
async def conditional_retrieve_node(
    state: ChatState,
    writer: StreamWriter
) -> ChatState:
    """Conditionally retrieve data based on simple analysis"""
    try:
        logger = state["logger"]
        
        if state.get("error"):
            return state
            
        analysis = state.get("query_analysis", {})
        
        # Skip retrieval if not needed
        if not analysis.get("needs_internal_data", False):
            logger.info("Skipping data retrieval - not needed for this query")
            state["search_results"] = []
            state["final_results"] = []
            return state
        
        logger.info("Internal data retrieval needed - proceeding with retrieval")
        writer({"event": "status", "data": {"status": "retrieving", "message": "Retrieving relevant data..."}})
        
        # Use original query for retrieval
        retrieval_service = state["retrieval_service"]
        arango_service = state["arango_service"]
        
        results = await retrieval_service.search_with_filters(
            queries=[state["query"]],
            org_id=state["org_id"],
            user_id=state["user_id"],
            limit=state["limit"],
            filter_groups=state["filters"],
            arango_service=arango_service,
        )
        
        status_code = results.get("status_code", 200)
        if status_code in [202, 500, 503]:
            state["error"] = {
                "status_code": status_code,
                "status": results.get("status", "error"),
                "message": results.get("message", "No results found"),
            }
            return state
        
        search_results = results.get("searchResults", [])
        logger.info(f"Retrieved {len(search_results)} documents from internal data")
        
        # Simple deduplication
        seen_ids = set()
        final_results = []
        for result in search_results:
            result_id = result["metadata"].get("_id")
            if result_id not in seen_ids:
                seen_ids.add(result_id)
                final_results.append(result)
        
        state["search_results"] = search_results
        state["final_results"] = final_results[:state["limit"]]
        
        return state
        
    except Exception as e:
        logger.error(f"Error in conditional retrieval node: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state


# 3. User Info Node (unchanged)
async def get_user_info_node(state: ChatState) -> ChatState:
    """Fetch user info if needed"""
    try:
        logger = state["logger"]
        arango_service = state["arango_service"]

        if state.get("error") or not state["send_user_info"]:
            return state

        user_task = arango_service.get_user_by_user_id(state["user_id"])
        org_task = arango_service.get_document(state["org_id"], CollectionNames.ORGS.value)

        user_info, org_info = await asyncio.gather(user_task, org_task)

        state["user_info"] = user_info
        state["org_info"] = org_info
        return state
    except Exception as e:
        logger.error(f"Error in user info node: {str(e)}", exc_info=True)
        return state


# 4. Clean Prompt Creation - Let LLM decide on tools naturally
def prepare_clean_prompt_node(
    state: ChatState,
    writer: StreamWriter
) -> ChatState:
    """Create a clean prompt that lets the LLM decide on tool usage naturally"""
    try:
        logger = state["logger"]
        if state.get("error"):
            return state

        # Build context based on available data
        context_parts = []
        
        # Add internal data context if retrieved
        if state.get("final_results"):
            from jinja2 import Template
            template = Template(qna_prompt)
            
            # Format user info
            user_data = ""
            if state["send_user_info"] and state.get("user_info") and state.get("org_info"):
                if state["org_info"].get("accountType") in [AccountType.ENTERPRISE.value, AccountType.BUSINESS.value]:
                    user_data = (
                        f"User: {state['user_info'].get('fullName', 'a user')} "
                        f"({state['user_info'].get('designation', '')}) "
                        f"from {state['org_info'].get('name', 'the organization')}"
                    )
            
            internal_context = template.render(
                user_data=user_data,
                query=state["query"],
                rephrased_queries=[],
                chunks=state["final_results"],
            )
            context_parts.append(internal_context)
        
        # Build clean system message
        system_content = state.get("system_prompt") or "You are an intelligent AI assistant"
        
        # Add natural tool guidance (no forced logic)
        tools_config = state.get("tools", [])
        if tools_config:
            from app.modules.agents.qna.tools import get_agent_tools
            tools = get_agent_tools(state)
            if tools:
                tool_descriptions = []
                for tool in tools:
                    tool_descriptions.append(f"- {tool.name}: {tool.description}")
                
                system_content += f"""

You have access to the following tools that you can use when appropriate:
{chr(10).join(tool_descriptions)}

Use these tools when they would be helpful to better answer the user's question or fulfill their request. Think about what the user is asking for and choose the right tools for the job."""
        
        # Create messages
        messages = [SystemMessage(content=system_content)]
        
        # Add conversation history
        for conversation in state.get("previous_conversations", []):
            if conversation.get("role") == "user_query":
                messages.append(HumanMessage(content=conversation.get("content")))
            elif conversation.get("role") == "bot_response":
                messages.append(AIMessage(content=conversation.get("content")))
        
        # Add current query with context
        if context_parts:
            full_prompt = "\n\n".join(context_parts)
        else:
            full_prompt = f"User Query: {state['query']}\n\nPlease provide a helpful response."
        
        messages.append(HumanMessage(content=full_prompt))
        
        state["messages"] = messages
        logger.debug(f"Prepared clean prompt with {len(messages)} messages")
        
        return state
    except Exception as e:
        logger.error(f"Error in clean prompt preparation: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state


# 5. Clean Agent Node - No forced tool logic, pure LLM decision making
async def clean_agent_node(
    state: ChatState,
    writer: StreamWriter
) -> ChatState:
    """Clean agent that lets LLM naturally decide on tool usage"""
    try:
        logger = state["logger"]
        llm = state["llm"]

        writer({"event": "status", "data": {"status": "thinking", "message": "Processing your request..."}})

        if state.get("error"):
            return state

        # Get all available tools (no filtering)
        tools_config = state.get("tools", [])
        
        if tools_config:
            from app.modules.agents.qna.tools import get_agent_tools
            tools = get_agent_tools(state)
            
            logger.debug(f"Available tools: {[tool.name for tool in tools]}")
            
            try:
                llm_with_tools = llm.bind_tools(tools)
            except (NotImplementedError, AttributeError) as e:
                logger.warning(f"LLM does not support tool binding: {e}")
                llm_with_tools = llm
                tools = []
        else:
            llm_with_tools = llm
            tools = []
            logger.debug("No tools available")

        # Call the LLM - let it decide naturally
        cleaned_messages = _clean_message_history(state["messages"])
        response = await llm_with_tools.ainvoke(cleaned_messages)
        
        # Add the response to messages
        state["messages"].append(response)
        
        # Check if the response contains tool calls
        if hasattr(response, 'tool_calls') and response.tool_calls:
            logger.debug(f"Agent decided to call {len(response.tool_calls)} tools")
            for tool_call in response.tool_calls:
                tool_name = tool_call.get("name") if isinstance(tool_call, dict) else getattr(tool_call, 'name', 'unknown')
                logger.debug(f"  - {tool_name}")
            state["pending_tool_calls"] = True
        else:
            logger.debug("Agent decided to provide final response without tools")
            state["pending_tool_calls"] = False
            
            if hasattr(response, 'content'):
                state["response"] = response.content
            else:
                state["response"] = str(response)

        return state
        
    except Exception as e:
        logger.error(f"Error in clean agent node: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state


# 6. Clean Tool Execution Node - Execute whatever tools the LLM chose
async def clean_tool_execution_node(
    state: ChatState,
    writer: StreamWriter
) -> ChatState:
    """Execute the tools that the LLM chose to use"""
    try:
        logger = state["logger"]
        
        writer({"event": "status", "data": {"status": "using_tools", "message": "Executing tools..."}})

        if state.get("error"):
            return state

        # Get the last AI message with tool calls
        last_ai_message = None
        for msg in reversed(state["messages"]):
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                last_ai_message = msg
                break
        
        if not last_ai_message:
            logger.warning("No tool calls found")
            state["pending_tool_calls"] = False
            return state

        tool_calls = last_ai_message.tool_calls
        
        # Get the tools
        from app.modules.agents.qna.tools import get_agent_tools
        tools = get_agent_tools(state)
        
        # Execute tools and create ToolMessage objects
        tool_messages = []
        tool_results = []
        
        for tool_call in tool_calls:
            tool_name = tool_call.get("name") if isinstance(tool_call, dict) else tool_call.name
            tool_args = tool_call.get("args", {}) if isinstance(tool_call, dict) else tool_call.args
            tool_id = tool_call.get("id") if isinstance(tool_call, dict) else tool_call.id
            
            # Handle function call format
            if hasattr(tool_call, 'function'):
                tool_name = tool_call.function.name
                tool_args = tool_call.function.arguments
                if isinstance(tool_args, str):
                    import json
                    tool_args = json.loads(tool_args)
            
            # Find and execute tool
            tool = next((t for t in tools if t.name == tool_name), None)
            
            if tool:
                try:
                    logger.debug(f"Executing tool: {tool_name} with args: {tool_args}")
                    result = tool._run(**tool_args) if hasattr(tool, '_run') else tool.run(**tool_args)
                    
                    tool_results.append({
                        "tool_name": tool_name,
                        "result": result,
                        "status": "success",
                        "tool_id": tool_id
                    })
                    
                    # Create ToolMessage
                    tool_message = ToolMessage(content=str(result), tool_call_id=tool_id)
                    tool_messages.append(tool_message)
                    
                    logger.debug(f"Tool {tool_name} executed successfully")
                    
                except Exception as e:
                    error_result = f"Error: {str(e)}"
                    tool_results.append({
                        "tool_name": tool_name,
                        "result": error_result,
                        "status": "error",
                        "tool_id": tool_id
                    })
                    
                    tool_message = ToolMessage(content=error_result, tool_call_id=tool_id)
                    tool_messages.append(tool_message)
                    
                    logger.error(f"Tool {tool_name} failed: {e}")
            else:
                logger.warning(f"Tool {tool_name} not found")
        
        # Add tool messages to conversation
        state["messages"].extend(tool_messages)
        state["tool_results"] = tool_results
        state["pending_tool_calls"] = False
        
        logger.debug(f"Executed {len(tool_results)} tools")
        return state
        
    except Exception as e:
        logger.error(f"Error in clean tool execution: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state


# 7. Clean Final Response Node - Simple final response generation
async def clean_final_response_node(
    state: ChatState,
    writer: StreamWriter
) -> ChatState:
    """Generate clean final response"""
    try:
        logger = state["logger"]
        llm = state["llm"]

        writer({"event": "status", "data": {"status": "finalizing", "message": "Generating final response..."}})

        if state.get("error"):
            return state

        # If we already have a response and no pending tool calls, we're done
        if state.get("response") and not state.get("pending_tool_calls", False):
            final_content = state["response"]
        else:
            # Validate messages before final LLM call
            validated_messages = []
            messages = state["messages"]
            
            for i, msg in enumerate(messages):
                if isinstance(msg, (SystemMessage, HumanMessage, AIMessage)):
                    validated_messages.append(msg)
                elif hasattr(msg, 'tool_call_id'):
                    # Only keep tool messages that follow AI messages with tool_calls
                    if (i > 0 and isinstance(messages[i-1], AIMessage) and 
                        hasattr(messages[i-1], 'tool_calls') and messages[i-1].tool_calls):
                        tool_call_ids = {tc.get('id') for tc in messages[i-1].tool_calls}
                        if msg.tool_call_id in tool_call_ids:
                            validated_messages.append(msg)
            
            # Get final response from LLM
            response = await llm.ainvoke(validated_messages)
            final_content = response.content if hasattr(response, 'content') else str(response)
        
        # Process citations if we have internal data
        if state.get("final_results"):
            final_content = process_citations(final_content, state["final_results"])
        
        state["response"] = final_content
        writer({"event": "complete", "data": {"answer": final_content}})
        
        logger.debug("Final response generated successfully")
        return state
        
    except Exception as e:
        logger.error(f"Error in clean final response: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state


# Helper function
def _clean_message_history(messages):
    """Clean message history for LLM compatibility"""
    cleaned = []
    for i, msg in enumerate(messages):
        if hasattr(msg, 'tool_call_id'):
            if i > 0:
                prev_msg = messages[i-1]
                if (hasattr(prev_msg, 'tool_calls') and prev_msg.tool_calls or
                    hasattr(prev_msg, 'additional_kwargs') and prev_msg.additional_kwargs.get('tool_calls')):
                    cleaned.append(msg)
        else:
            cleaned.append(msg)
    return cleaned


# Routing functions
def should_continue(state: ChatState) -> Literal["execute_tools", "final"]:
    """Route based on pending tool calls"""
    return "execute_tools" if state.get("pending_tool_calls", False) else "final"

def check_for_error(state: ChatState) -> Literal["error", "continue"]:
    """Check for errors"""
    return "error" if state.get("error") else "continue"