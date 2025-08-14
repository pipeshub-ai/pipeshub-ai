"""
Modified nodes.py that integrates with the unified tool registry
for proper multi-service tool execution.
"""

import asyncio
import json
from datetime import datetime
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import StreamWriter

from app.config.constants.arangodb import AccountType, CollectionNames
from app.modules.agents.qna.chat_state import ChatState

# Import the unified tool registry
from app.modules.agents.qna.tool_registry import (
    execute_tool_simple,
    get_agent_tool_registry,
    get_agent_tools,
    get_tool_results_summary,
    get_tool_usage_guidance,
)
from app.modules.qna.prompt_templates import qna_prompt
from app.utils.citations import fix_json_string, process_citations
from app.utils.streaming import stream_llm_response


# 1. Query Analysis Node
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


# 2. Conditional Retrieval Node
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

        # Skip retrieval based on analysis
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


# 3. User Info Node
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


# 4. Initialize Tool Registry Node (NEW)
async def initialize_tool_registry_node(
    state: ChatState,
    writer: StreamWriter
) -> ChatState:
    """Initialize the unified tool registry with all available tools"""
    try:
        logger = state["logger"]

        writer({"event": "status", "data": {"status": "initializing_tools", "message": "Setting up available tools..."}})

        # Get or create the tool registry
        registry = get_agent_tool_registry(logger)

        # Initialize all tools
        available_tools = await registry.initialize_all_tools(
            org_id=state["org_id"],
            user_id=state["user_id"],
            config_service=state["config_service"],
            arango_service=state["arango_service"]
        )

        # Store available tools in state
        state["available_tools"] = [tool.name for tool in available_tools]
        state["registry_tool_instances"] = registry.tool_instances

        logger.info(f"Tool registry initialized with {len(available_tools)} tools")
        return state

    except Exception as e:
        logger.error(f"Error initializing tool registry: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 500, "detail": f"Tool registry initialization failed: {str(e)}"}
        return state


# 5. Clean Prompt Creation
def prepare_clean_prompt_node(
    state: ChatState,
    writer: StreamWriter
) -> ChatState:
    """Create a clean prompt that presents all available tools to the LLM"""
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

        # Get ALL available tools from unified registry
        tools = get_agent_tools(state)

        if tools:
            # Simple tool presentation
            tool_descriptions = []
            for tool in tools:
                tool_descriptions.append(f"- {tool.name}: {tool.description}")

            system_content += f"""

You have access to the following tools:

{chr(10).join(tool_descriptions)}

{get_tool_usage_guidance()}"""

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
        logger.debug(f"Prepared prompt with {len(messages)} messages and {len(tools)} available tools")

        return state
    except Exception as e:
        logger.error(f"Error in prompt preparation: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state


# 6. Agent Node
async def agent_node(
    state: ChatState,
    writer: StreamWriter
) -> ChatState:
    """Pure agent that lets LLM decide everything using unified tool registry"""
    try:
        logger = state["logger"]
        llm = state["llm"]

        writer({"event": "status", "data": {"status": "thinking", "message": "Processing your request..."}})

        if state.get("error"):
            return state

        # Get ALL available tools from unified registry
        tools = get_agent_tools(state)

        if tools:
            logger.debug(f"Providing {len(tools)} tools to LLM for autonomous decision making")
            try:
                llm_with_tools = llm.bind_tools(tools)
            except (NotImplementedError, AttributeError) as e:
                logger.warning(f"LLM does not support tool binding: {e}")
                llm_with_tools = llm
                tools = []
        else:
            llm_with_tools = llm
            logger.debug("No tools available")

        # Add previous tool results context if available
        if state.get("all_tool_results"):
            tool_context = "\n\n" + get_tool_results_summary(state)

            # Add context to the last human message
            if state["messages"] and isinstance(state["messages"][-1], HumanMessage):
                state["messages"][-1].content += tool_context
                logger.debug(f"Added tool execution context from {len(state['all_tool_results'])} previous results")

        # Call the LLM
        cleaned_messages = _clean_message_history(state["messages"])
        response = await llm_with_tools.ainvoke(cleaned_messages)

        # Add the response to messages
        state["messages"].append(response)

        # Check LLM's decision on tool usage
        if hasattr(response, 'tool_calls') and response.tool_calls:
            logger.debug(f"LLM autonomously decided to use {len(response.tool_calls)} tools")
            for tool_call in response.tool_calls:
                tool_name = tool_call.get("name") if isinstance(tool_call, dict) else getattr(tool_call, 'name', 'unknown')
                logger.debug(f"  - {tool_name}")
            state["pending_tool_calls"] = True
        else:
            logger.debug("LLM autonomously decided to provide final response without tools")
            state["pending_tool_calls"] = False

            if hasattr(response, 'content'):
                state["response"] = response.content
            else:
                state["response"] = str(response)

        logger.debug(f"🔥 Agent response: {state.get('response', 'No response yet')}")

        return state

    except Exception as e:
        logger.error(f"Error in agent node: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state


# 7. UNIFIED Tool Execution Node
async def tool_execution_node(
    state: ChatState,
    writer: StreamWriter
) -> ChatState:
    """UNIFIED tool execution using the unified registry"""
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

        # Get available tools from unified registry
        tools = get_agent_tools(state)
        tools_by_name = {tool.name: tool for tool in tools}

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
                    tool_args = json.loads(tool_args)

            try:
                result = None

                # UNIFIED: Use the unified registry execution
                if tool_name in tools_by_name:
                    tool = tools_by_name[tool_name]
                    logger.debug(f"🚀 Executing tool: {tool_name} with args: {tool_args}")

                    # Use the unified execution function
                    result = await execute_tool_simple(tool, tool_args, logger)

                    # Store detailed tool result
                    tool_result = {
                        "tool_name": tool_name,
                        "result": result,
                        "status": "success",
                        "tool_id": tool_id,
                        "args": tool_args,
                        "execution_timestamp": datetime.now().isoformat()
                    }
                    tool_results.append(tool_result)

                    # Create ToolMessage
                    tool_message = ToolMessage(content=str(result), tool_call_id=tool_id)
                    tool_messages.append(tool_message)

                    logger.debug(f"🚀 Tool {tool_name} executed successfully")

                else:
                    logger.warning(f"🚀 Tool {tool_name} not found in available tools")
                    error_result = f"Tool '{tool_name}' not found in available tools"
                    tool_result = {
                        "tool_name": tool_name,
                        "result": error_result,
                        "status": "error",
                        "tool_id": tool_id,
                        "args": tool_args,
                        "execution_timestamp": datetime.now().isoformat(),
                        "error_details": "Tool not found"
                    }
                    tool_results.append(tool_result)

                    tool_message = ToolMessage(content=error_result, tool_call_id=tool_id)
                    tool_messages.append(tool_message)

            except Exception as e:
                logger.error(f"🚀 Tool {tool_name} execution failed: {e}", exc_info=True)
                error_result = f"Error executing {tool_name}: {str(e)}"
                tool_result = {
                    "tool_name": tool_name,
                    "result": error_result,
                    "status": "error",
                    "tool_id": tool_id,
                    "args": tool_args,
                    "execution_timestamp": datetime.now().isoformat(),
                    "error_details": str(e)
                }
                tool_results.append(tool_result)

                tool_message = ToolMessage(content=error_result, tool_call_id=tool_id)
                tool_messages.append(tool_message)

        # Add tool messages to conversation
        state["messages"].extend(tool_messages)

        # Store tool results
        state["tool_results"] = tool_results

        # Accumulate all tool results for the session
        if "all_tool_results" not in state:
            state["all_tool_results"] = []
        state["all_tool_results"].extend(tool_results)

        # Reset pending tool calls
        state["pending_tool_calls"] = False

        logger.debug(f"🚀 Executed {len(tool_results)} tools. Session total: {len(state['all_tool_results'])}")
        return state

    except Exception as e:
        logger.error(f"🚀 Error in tool execution: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state


# 8. Final Response Node (unchanged but using unified registry for summaries)
async def final_response_node(
    state: ChatState,
    writer: StreamWriter
) -> ChatState:
    """Generate final response"""
    try:
        logger = state["logger"]
        llm = state["llm"]

        writer({"event": "status", "data": {"status": "finalizing", "message": "Generating final response..."}})

        if state.get("error"):
            return state

        # Check for existing response
        existing_response = state.get("response")
        use_existing_response = (
            existing_response and
            not state.get("pending_tool_calls", False)
        )

        if use_existing_response:
            logger.debug(f"Using existing response: {len(str(existing_response))} chars")

            # Stream the existing response in chunks
            writer({"event": "status", "data": {"status": "delivering", "message": "Delivering response..."}})

            # Normalize response format
            final_content = _normalize_response_format(existing_response)

            # Process citations if available
            if state.get("final_results"):
                cited_answer = process_citations(final_content["answer"], state["final_results"])
                if isinstance(cited_answer, str):
                    final_content["answer"] = cited_answer
                elif isinstance(cited_answer, dict) and "answer" in cited_answer:
                    final_content = cited_answer

            # Send content in chunks
            chunk_size = 50
            answer_text = final_content.get("answer", "")
            for i in range(0, len(answer_text), chunk_size):
                chunk = answer_text[i:i + chunk_size]
                writer({"event": "answer_chunk", "data": {"chunk": chunk}})
                await asyncio.sleep(0.01)

            # Send complete event
            completion_data = final_content
            writer({"event": "complete", "data": completion_data})

            state["response"] = _clean_response(final_content)
            state["completion_data"] = completion_data

            logger.debug(f"Delivered existing response: {len(answer_text)} chars")
            return state

        # Generate new response
        logger.debug("No usable response found, generating new response with LLM")

        # Convert LangChain messages to dict format
        validated_messages = []
        for msg in state.get("messages", []):
            if isinstance(msg, SystemMessage):
                validated_messages.append({"role": "system", "content": msg.content})
            elif isinstance(msg, HumanMessage):
                validated_messages.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                validated_messages.append({"role": "assistant", "content": msg.content})

        # Add tool summary if available (using unified registry)
        if state.get("all_tool_results"):
            tool_summary = get_tool_results_summary(state)

            if validated_messages and validated_messages[-1]["role"] == "user":
                validated_messages[-1]["content"] += f"\n\nTool Execution Results:\n{tool_summary}"
            else:
                validated_messages.append({
                    "role": "user",
                    "content": f"Based on the tool execution results:\n{tool_summary}\n\nPlease provide a comprehensive response."
                })

        # Get final results for citations
        final_results = state.get("final_results", [])

        writer({"event": "status", "data": {"status": "generating", "message": "Generating response..."}})

        # Use stream_llm_response for new generation
        final_content = ""
        completion_data = None

        try:
            async for stream_event in stream_llm_response(llm, validated_messages, final_results):
                event_type = stream_event["event"]
                event_data = stream_event["data"]
                logger.debug(f"🔥 Stream event: {event_type}")

                # Forward all events from stream_llm_response
                writer({"event": event_type, "data": event_data})

                # Track the final answer
                if event_type == "complete":
                    final_content = event_data
                    completion_data = event_data
                elif event_type == "answer_chunk":
                    final_content += event_data

        except Exception as stream_error:
            logger.error(f"stream_llm_response failed: {stream_error}")

            # Fallback to direct LLM call
            try:
                response = await llm.ainvoke(validated_messages)
                fallback_content = response.content if hasattr(response, 'content') else str(response)

                # Process citations
                if final_results:
                    cited_fallback = process_citations(fallback_content, final_results)
                    if isinstance(cited_fallback, str):
                        fallback_content = cited_fallback
                    elif isinstance(cited_fallback, dict):
                        fallback_content = cited_fallback.get("answer", fallback_content)

                # Send as chunks
                chunk_size = 100
                for i in range(0, len(fallback_content), chunk_size):
                    chunk = fallback_content[i:i + chunk_size]
                    writer({"event": "answer_chunk", "data": {"chunk": chunk}})
                    await asyncio.sleep(0.02)

                # Create completion data
                citations = [
                    {
                        "citationId": result["metadata"].get("_id"),
                        "content": result.get("content", ""),
                        "metadata": result.get("metadata", {}),
                        "citationType": result.get("citationType", "vectordb|document"),
                        "chunkIndex": i + 1
                    }
                    for i, result in enumerate(final_results)
                ]

                completion_data = {
                    "answer": fallback_content,
                    "citations": citations,
                    "confidence": "Medium",
                    "reason": "Fallback response generation"
                }
                writer({"event": "complete", "data": completion_data})
                final_content = completion_data

            except Exception as fallback_error:
                logger.error(f"Fallback generation also failed: {fallback_error}")
                error_content = "I apologize, but I encountered an issue generating a response. Please try again."
                error_response = {
                    "answer": error_content,
                    "citations": [],
                    "confidence": "Low",
                    "reason": "Error fallback"
                }
                writer({"event": "answer_chunk", "data": {"chunk": error_content}})
                writer({"event": "complete", "data": error_response})
                final_content = error_response
                completion_data = error_response

        # Store final response
        state["response"] = _clean_response(final_content)
        if completion_data:
            state["completion_data"] = _clean_response(completion_data)

        response_len = len(str(final_content))
        logger.debug(f"Generated new response: {response_len} chars")
        return state

    except Exception as e:
        logger.error(f"Error in final response: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        writer({"event": "error", "data": {"error": str(e)}})
        return state


# Helper functions (unchanged)
def _normalize_response_format(response) -> dict:
    """Normalize response to expected format"""
    if isinstance(response, str):
        return {
            "answer": response,
            "citations": [],
            "confidence": "High",
            "reason": "Direct response"
        }
    elif isinstance(response, dict):
        return {
            "answer": response.get("answer", str(response.get("content", response))),
            "citations": response.get("citations", []),
            "confidence": response.get("confidence", "Medium"),
            "reason": response.get("reason", "Processed response")
        }
    else:
        return {
            "answer": str(response),
            "citations": [],
            "confidence": "Low",
            "reason": "Converted response"
        }


def _clean_response(response) -> dict:
    """Clean the response to ensure it is in the expected format"""
    if isinstance(response, str):
        try:
            cleaned_content = response.strip()
            if cleaned_content.startswith('"') and cleaned_content.endswith('"'):
                cleaned_content = cleaned_content[1:-1].replace('\\"', '"')

            cleaned_content = cleaned_content.replace("\\n", "\n").replace("\\t", "\t")
            cleaned_content = fix_json_string(cleaned_content)

            response_data = json.loads(cleaned_content)
            return _normalize_response_format(response_data)
        except (json.JSONDecodeError, Exception):
            return _normalize_response_format(response)
    else:
        return _normalize_response_format(response)


def _validate_and_fix_message_sequence(messages) -> list:
    """Validate and fix message sequence"""
    validated = []
    pending_tool_calls = {}

    for msg in messages:
        if isinstance(msg, (SystemMessage, HumanMessage)):
            if isinstance(msg, HumanMessage):
                pending_tool_calls.clear()
            validated.append(msg)

        elif isinstance(msg, AIMessage):
            validated.append(msg)
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_id = tc.get('id') if isinstance(tc, dict) else getattr(tc, 'id', None)
                    if tool_id:
                        pending_tool_calls[tool_id] = True

        elif hasattr(msg, 'tool_call_id'):
            if msg.tool_call_id in pending_tool_calls:
                validated.append(msg)
                pending_tool_calls.pop(msg.tool_call_id, None)

    # Remove unresolved tool calls
    if pending_tool_calls:
        final_validated = []
        for msg in validated:
            if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
                has_unresolved = False
                for tc in msg.tool_calls:
                    tool_id = tc.get('id') if isinstance(tc, dict) else getattr(tc, 'id', None)
                    if tool_id and tool_id in pending_tool_calls:
                        has_unresolved = True
                        break

                if not has_unresolved:
                    final_validated.append(msg)
            else:
                final_validated.append(msg)

        validated = final_validated

    return validated


def _clean_message_history(messages) -> list:
    """Clean message history for LLM compatibility"""
    validated_messages = _validate_and_fix_message_sequence(messages)
    cleaned = []

    for i, msg in enumerate(validated_messages):
        if isinstance(msg, (SystemMessage, HumanMessage, AIMessage)):
            cleaned.append(msg)

        elif hasattr(msg, 'tool_call_id'):
            found_matching_ai = False
            for j in range(i-1, -1, -1):
                prev_msg = validated_messages[j]
                if isinstance(prev_msg, AIMessage):
                    if hasattr(prev_msg, 'tool_calls') and prev_msg.tool_calls:
                        tool_call_ids = []
                        for tc in prev_msg.tool_calls:
                            if isinstance(tc, dict):
                                tool_call_ids.append(tc.get('id'))
                            else:
                                tool_call_ids.append(getattr(tc, 'id', None))

                        if msg.tool_call_id in tool_call_ids:
                            found_matching_ai = True
                            break
                    else:
                        break
                elif hasattr(prev_msg, 'tool_call_id'):
                    continue
                else:
                    break

            if found_matching_ai:
                cleaned.append(msg)

    return cleaned


# Routing functions
def should_continue(state: ChatState) -> Literal["execute_tools", "final"]:
    """Simple routing based on LLM's tool call decision"""
    return "execute_tools" if state.get("pending_tool_calls", False) else "final"


def check_for_error(state: ChatState) -> Literal["error", "continue"]:
    """Simple error check"""
    return "error" if state.get("error") else "continue"
