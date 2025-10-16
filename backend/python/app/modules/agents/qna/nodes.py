"""
Enhanced Planning-Based Agent Nodes - COMPLETE VERSION
Supports planning, execution, adaptation, and beautiful response formatting
"""

import asyncio
import json
from datetime import datetime
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import StreamWriter

from app.config.constants.arangodb import CollectionNames
from app.modules.agents.qna.chat_state import ChatState
from app.modules.qna.agent_prompt import (
    create_agent_messages,
    detect_response_mode,
)
from app.utils.citations import fix_json_string, process_citations
from app.utils.streaming import stream_llm_response

# ============================================================================
# PHASE 1: ENHANCED QUERY ANALYSIS
# ============================================================================

async def analyze_query_node(state: ChatState, writer: StreamWriter) -> ChatState:
    """Analyze query complexity, follow-ups, and determine retrieval needs"""
    try:
        logger = state["logger"]
        writer({"event": "status", "data": {"status": "analyzing", "message": "🧠 Analyzing your request..."}})

        query = state["query"].lower()
        previous_conversations = state.get("previous_conversations", [])

        # Enhanced follow-up detection
        follow_up_patterns = [
            "tell me more", "what about", "and the", "also", "additionally",
            "the second", "the first", "the third", "next one", "previous",
            "can you elaborate", "more details", "explain further", "what else",
            "continue", "go on", "expand on", "about that", "about it",
            "more info", "details on"
        ]

        # Check for pronouns that suggest follow-ups
        pronoun_patterns = ["it", "that", "those", "these", "them", "this"]
        has_pronoun = any(f" {p} " in f" {query} " or query.startswith(f"{p} ") for p in pronoun_patterns)

        is_follow_up = (
            any(pattern in query for pattern in follow_up_patterns) or
            (has_pronoun and len(previous_conversations) > 0)
        )

        # Complexity detection
        complexity_indicators = {
            "multi_step": ["and then", "after that", "followed by", "once you", "first", "then", "finally", "next"],
            "conditional": ["if", "unless", "in case", "when", "should", "whether"],
            "comparison": ["compare", "vs", "versus", "difference between", "better than", "contrast"],
            "aggregation": ["all", "every", "each", "summarize", "total", "average", "list"],
            "creation": ["create", "make", "generate", "build", "draft", "compose"],
            "action": ["send", "email", "notify", "schedule", "update", "delete"]
        }

        detected_complexity = []
        for complexity_type, indicators in complexity_indicators.items():
            if any(indicator in query for indicator in indicators):
                detected_complexity.append(complexity_type)

        is_complex = len(detected_complexity) > 0

        # Internal data need detection
        has_kb_filter = bool(state.get("filters", {}).get("kb"))
        has_app_filter = bool(state.get("filters", {}).get("apps"))

        internal_keywords = [
            "our", "my", "company", "organization", "internal",
            "knowledge base", "documents", "files", "emails",
            "data", "records", "slack", "drive", "confluence",
            "jira", "policy", "procedure", "team", "project"
        ]

        needs_internal_data = False
        if is_follow_up and previous_conversations and not has_kb_filter and not has_app_filter:
            # For follow-ups, check if previous turn had internal data
            if previous_conversations:
                last_response = previous_conversations[-1].get("content", "")
                # If last response had citations or knowledge, might not need new retrieval
                needs_internal_data = "[" not in last_response  # Simple heuristic
            else:
                needs_internal_data = False
            logger.info(f"Follow-up detected - needs new retrieval: {needs_internal_data}")
        else:
            needs_internal_data = (
                has_kb_filter or
                has_app_filter or
                any(keyword in query for keyword in internal_keywords)
            )

        # Store analysis
        state["query_analysis"] = {
            "needs_internal_data": needs_internal_data,
            "is_follow_up": is_follow_up,
            "is_complex": is_complex,
            "complexity_types": detected_complexity,
            "requires_beautiful_formatting": True,  # Always format beautifully
            "reasoning": f"Follow-up: {is_follow_up}, Complex: {is_complex}, Types: {detected_complexity}"
        }

        logger.info(f"📊 Query analysis: follow_up={is_follow_up}, complex={is_complex}, data_needed={needs_internal_data}")
        if is_complex:
            logger.info(f"🔍 Complexity indicators: {', '.join(detected_complexity)}")

        return state

    except Exception as e:
        logger.error(f"Error in query analysis: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state


# ============================================================================
# PHASE 2: SMART RETRIEVAL
# ============================================================================

async def conditional_retrieve_node(state: ChatState, writer: StreamWriter) -> ChatState:
    """Smart retrieval based on query analysis"""
    try:
        logger = state["logger"]

        if state.get("error"):
            return state

        analysis = state.get("query_analysis", {})

        if not analysis.get("needs_internal_data", False):
            logger.info("⏭️ Skipping retrieval - using conversation context")
            state["search_results"] = []
            state["final_results"] = []
            return state

        logger.info("📚 Gathering knowledge sources...")
        writer({"event": "status", "data": {"status": "retrieving", "message": "📚 Gathering knowledge sources..."}})

        retrieval_service = state["retrieval_service"]
        arango_service = state["arango_service"]

        # Adjust limit based on complexity
        is_complex = analysis.get("is_complex", False)
        base_limit = state["limit"]
        adjusted_limit = min(base_limit * 2, 100) if is_complex else base_limit

        logger.debug(f"Using retrieval limit: {adjusted_limit} (complex: {is_complex})")

        results = await retrieval_service.search_with_filters(
            queries=[state["query"]],
            org_id=state["org_id"],
            user_id=state["user_id"],
            limit=adjusted_limit,
            filter_groups=state["filters"],
            arango_service=arango_service,
        )

        status_code = results.get("status_code", 200)
        if status_code in [202, 500, 503]:
            state["error"] = {
                "status_code": status_code,
                "status": results.get("status", "error"),
                "message": results.get("message", "Retrieval service unavailable"),
            }
            return state

        search_results = results.get("searchResults", [])
        logger.info(f"✅ Retrieved {len(search_results)} documents")

        # Deduplicate
        seen_ids = set()
        final_results = []
        for result in search_results:
            result_id = result["metadata"].get("_id")
            if result_id not in seen_ids:
                seen_ids.add(result_id)
                final_results.append(result)

        state["search_results"] = search_results
        state["final_results"] = final_results[:adjusted_limit]

        logger.debug(f"Final deduplicated results: {len(state['final_results'])}")

        return state

    except Exception as e:
        logger.error(f"Error in retrieval: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state


# ============================================================================
# PHASE 3: USER CONTEXT
# ============================================================================

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
        logger.error(f"Error fetching user info: {str(e)}", exc_info=True)
        return state


# ============================================================================
# PHASE 4: ENHANCED AGENT PROMPT PREPARATION
# ============================================================================

def prepare_agent_prompt_node(state: ChatState, writer: StreamWriter) -> ChatState:
    """Prepare enhanced agent prompt with dual-mode formatting instructions"""
    try:
        logger = state["logger"]
        if state.get("error"):
            return state

        logger.debug("🎯 Preparing agent prompt with dual-mode support")

        is_complex = state.get("query_analysis", {}).get("is_complex", False)
        complexity_types = state.get("query_analysis", {}).get("complexity_types", [])
        has_internal_knowledge = bool(state.get("final_results"))

        if is_complex:
            logger.info(f"🔍 Complex workflow detected: {', '.join(complexity_types)}")
            writer({"event": "status", "data": {"status": "thinking", "message": "🧠 Planning complex workflow..."}})

        # Determine expected output mode
        if has_internal_knowledge:
            expected_mode = "structured_with_citations"
            logger.info("📋 Expected output: Structured JSON with citations (internal knowledge available)")
        else:
            expected_mode = "markdown"
            logger.info("📝 Expected output: Beautiful Markdown (no internal knowledge)")

        # Store metadata
        state["expected_response_mode"] = expected_mode
        state["requires_planning"] = is_complex
        state["has_internal_knowledge"] = has_internal_knowledge

        # Create messages with planning context
        messages = create_agent_messages(state)

        # Get tools
        from app.modules.agents.qna.tool_registry import get_agent_tools
        tools = get_agent_tools(state)

        # Expose tool names for context
        try:
            state["available_tools"] = [tool.name for tool in tools] if tools else []
        except Exception:
            state["available_tools"] = []

        state["messages"] = messages

        logger.debug(f"✅ Prepared {len(messages)} messages with {len(tools)} tools")
        logger.debug(f"Planning required: {is_complex}, Expected mode: {expected_mode}")

        return state

    except Exception as e:
        logger.error(f"Error preparing prompt: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state


# ============================================================================
# PHASE 5: ENHANCED AGENT WITH DUAL-MODE AWARENESS
# ============================================================================

async def agent_node(state: ChatState, writer: StreamWriter) -> ChatState:
    """Agent with reasoning and dual-mode output capabilities"""
    try:
        logger = state["logger"]
        llm = state["llm"]

        if state.get("error"):
            return state

        # Check iteration and context
        iteration_count = len(state.get("all_tool_results", []))
        is_complex = state.get("requires_planning", False)
        has_internal_knowledge = state.get("has_internal_knowledge", False)

        # Status messages
        if iteration_count == 0 and is_complex:
            writer({"event": "status", "data": {"status": "planning", "message": "📋 Creating execution plan..."}})
        elif iteration_count > 0:
            writer({"event": "status", "data": {"status": "adapting", "message": f"🔄 Adapting plan (step {iteration_count + 1})..."}})
        else:
            writer({"event": "status", "data": {"status": "thinking", "message": "💭 Processing your request..."}})

        # Get tools
        from app.modules.agents.qna.tool_registry import get_agent_tools
        tools = get_agent_tools(state)

        if tools:
            logger.debug(f"🛠️ Agent has {len(tools)} tools available")
            try:
                llm_with_tools = llm.bind_tools(tools)
            except (NotImplementedError, AttributeError) as e:
                logger.warning(f"LLM does not support tool binding: {e}")
                llm_with_tools = llm
                tools = []
        else:
            llm_with_tools = llm

        # Add tool results context with planning insights
        if state.get("all_tool_results"):
            from app.modules.agents.qna.tool_registry import get_tool_results_summary
            tool_summary = get_tool_results_summary(state)

            tool_context = f"\n\n## Execution Progress\n{tool_summary}"
            tool_context += "\n\n💡 **Adaptation Point**: Review results. Adjust plan or provide final answer."

            # Remind about output format
            if has_internal_knowledge:
                tool_context += "\n\n⚠️ **Remember**: You have internal knowledge sources available. If you used them, respond in Structured JSON with citations."
            else:
                tool_context += "\n\n💡 **Output**: Use Beautiful Markdown format for your final response."

            if state["messages"] and isinstance(state["messages"][-1], HumanMessage):
                state["messages"][-1].content += tool_context

        # Clean messages
        cleaned_messages = _clean_message_history(state["messages"])

        # Call LLM
        logger.debug(f"🤖 Invoking LLM (iteration {iteration_count})")
        response = await llm_with_tools.ainvoke(cleaned_messages)

        # Add response to messages
        state["messages"].append(response)

        # Check for tool calls
        if hasattr(response, 'tool_calls') and response.tool_calls:
            tool_count = len(response.tool_calls)
            logger.info(f"🔧 Agent decided to use {tool_count} tools")

            # Log which tools
            tool_names = []
            for tc in response.tool_calls:
                tool_name = tc.get("name") if isinstance(tc, dict) else tc.name
                tool_names.append(tool_name)
            logger.debug(f"Tools to execute: {', '.join(tool_names)}")

            state["pending_tool_calls"] = True
        else:
            logger.info("✅ Agent providing final response")
            state["pending_tool_calls"] = False

            if hasattr(response, 'content'):
                response_content = response.content
            else:
                response_content = str(response)

            # Detect mode
            mode, parsed_content = detect_response_mode(response_content)
            logger.info(f"📄 Response mode detected: {mode}")

            state["response"] = parsed_content
            state["response_mode"] = mode

        return state

    except Exception as e:
        logger.error(f"Error in agent: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state


# ============================================================================
# PHASE 6: TOOL EXECUTION
# ============================================================================

async def tool_execution_node(state: ChatState, writer: StreamWriter) -> ChatState:
    """Execute tools with planning context"""
    try:
        logger = state["logger"]

        iteration = len(state.get("all_tool_results", []))
        writer({"event": "status", "data": {"status": "executing", "message": f"⚙️ Executing tools (step {iteration + 1})..."}})

        if state.get("error"):
            return state

        # Get last AI message with tool calls
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

        # Get available tools
        from app.modules.agents.qna.tool_registry import get_agent_tools
        tools = get_agent_tools(state)
        tools_by_name = {tool.name: tool for tool in tools}

        tool_messages = []
        tool_results = []

        for tool_call in tool_calls:
            tool_name = tool_call.get("name") if isinstance(tool_call, dict) else tool_call.name
            tool_args = tool_call.get("args", {}) if isinstance(tool_call, dict) else tool_call.args
            tool_id = tool_call.get("id") if isinstance(tool_call, dict) else tool_call.id

            try:
                result = None

                if tool_name in tools_by_name:
                    tool = tools_by_name[tool_name]
                    logger.info(f"▶️ Executing: {tool_name}")
                    logger.debug(f"  Args: {tool_args}")

                    result = tool._run(**tool_args) if hasattr(tool, '_run') else tool.run(**tool_args)

                    # Log result preview
                    result_preview = str(result)[:150] + "..." if len(str(result)) > 150 else str(result)
                    logger.debug(f"  Result preview: {result_preview}")
                else:
                    logger.warning(f"Tool not found: {tool_name}")
                    result = json.dumps({
                        "status": "error",
                        "message": f"Tool '{tool_name}' not found in registry"
                    })

                tool_result = {
                    "tool_name": tool_name,
                    "result": result,
                    "status": "success" if "error" not in str(result).lower() else "error",
                    "tool_id": tool_id,
                    "args": tool_args,
                    "execution_timestamp": datetime.now().isoformat(),
                    "iteration": iteration
                }
                tool_results.append(tool_result)

                tool_message = ToolMessage(content=str(result), tool_call_id=tool_id)
                tool_messages.append(tool_message)

                logger.info(f"✅ {tool_name} executed successfully")

            except Exception as e:
                error_result = f"Error executing {tool_name}: {str(e)}"
                logger.error(f"❌ {tool_name} failed: {e}")

                tool_result = {
                    "tool_name": tool_name,
                    "result": error_result,
                    "status": "error",
                    "tool_id": tool_id,
                    "args": tool_args,
                    "execution_timestamp": datetime.now().isoformat(),
                    "error_details": str(e),
                    "iteration": iteration
                }
                tool_results.append(tool_result)

                tool_message = ToolMessage(content=error_result, tool_call_id=tool_id)
                tool_messages.append(tool_message)

        # Add to messages
        state["messages"].extend(tool_messages)
        state["tool_results"] = tool_results

        # Accumulate all results
        if "all_tool_results" not in state:
            state["all_tool_results"] = []
        state["all_tool_results"].extend(tool_results)

        state["pending_tool_calls"] = False

        logger.info(f"✅ Executed {len(tool_results)} tools in iteration {iteration}")
        logger.debug(f"Total tools executed: {len(state['all_tool_results'])}")

        return state

    except Exception as e:
        logger.error(f"Error in tool execution: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state


# ============================================================================
# PHASE 7: ENHANCED FINAL RESPONSE WITH DUAL-MODE SUPPORT
# ============================================================================

# 7. Fixed Final Response Node - Correct Streaming Format
async def final_response_node(
    state: ChatState,
    writer: StreamWriter
) -> ChatState:
    """Generate final response with correct streaming format"""
    try:
        logger = state["logger"]
        llm = state["llm"]

        writer({"event": "status", "data": {"status": "finalizing", "message": "Generating final response..."}})

        if state.get("error"):
            return state

        # Check for existing response from agent
        existing_response = state.get("response")
        use_existing_response = (
            existing_response and
            not state.get("pending_tool_calls", False)
        )

        if use_existing_response:
            logger.debug(f"Using existing response: {len(str(existing_response))} chars")

            writer({"event": "status", "data": {"status": "delivering", "message": "Delivering response..."}})

            # Normalize response format
            final_content = _normalize_response_format(existing_response)

            # Process citations if available
            if state.get("final_results"):
                cited_answer = process_citations(
                    final_content["answer"],
                    state["final_results"],
                    [],
                    from_agent=True
                )

                if isinstance(cited_answer, str):
                    final_content["answer"] = cited_answer
                elif isinstance(cited_answer, dict) and "answer" in cited_answer:
                    final_content = cited_answer

            # **CRITICAL FIX**: Stream only the answer text, not the JSON structure
            answer_text = final_content.get("answer", "")
            chunk_size = 50

            # Stream answer in chunks
            for i in range(0, len(answer_text), chunk_size):
                chunk = answer_text[i:i + chunk_size]
                writer({"event": "answer_chunk", "data": {"chunk": chunk}})
                await asyncio.sleep(0.01)

            # **CRITICAL**: Send complete structure only at the end
            completion_data = {
                "answer": answer_text,
                "citations": final_content.get("citations", []),
                "confidence": final_content.get("confidence", "High"),
                "reason": final_content.get("reason", "Response generated"),
                "answerMatchType": final_content.get("answerMatchType", "Derived From Tool Execution"),
                "chunkIndexes": final_content.get("chunkIndexes", []),
                "workflowSteps": final_content.get("workflowSteps", [])
            }

            writer({"event": "complete", "data": completion_data})

            state["response"] = answer_text  # Store just the answer text
            state["completion_data"] = completion_data

            logger.debug(f"Delivered existing response: {len(answer_text)} chars")
            return state

        # Generate new response if needed
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

        # Add tool summary if available
        if state.get("all_tool_results"):
            from app.modules.agents.qna.tool_registry import get_tool_results_summary
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
        final_content = None

        try:
            async for stream_event in stream_llm_response(llm, validated_messages, final_results, logger):
                event_type = stream_event["event"]
                event_data = stream_event["data"]

                # **CRITICAL**: Forward streaming events as-is
                # stream_llm_response already sends answer_chunk and complete events correctly
                writer({"event": event_type, "data": event_data})

                # Track the final complete data
                if event_type == "complete":
                    final_content = event_data

        except Exception as stream_error:
            logger.error(f"stream_llm_response failed: {stream_error}")

            # Fallback to direct LLM call
            try:
                response = await llm.ainvoke(validated_messages)
                fallback_content = response.content if hasattr(response, 'content') else str(response)

                # Process citations
                if final_results:
                    cited_fallback = process_citations(fallback_content, final_results, [], from_agent=True)
                    if isinstance(cited_fallback, str):
                        fallback_content = cited_fallback
                    elif isinstance(cited_fallback, dict):
                        fallback_content = cited_fallback.get("answer", fallback_content)

                # **CRITICAL**: Stream answer text only
                chunk_size = 100
                for i in range(0, len(fallback_content), chunk_size):
                    chunk = fallback_content[i:i + chunk_size]
                    writer({"event": "answer_chunk", "data": {"chunk": chunk}})
                    await asyncio.sleep(0.02)

                # Create completion data with proper format
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
                    "reason": "Fallback response generation",
                    "answerMatchType": "Derived From Tool Execution" if state.get("all_tool_results") else "Direct Response",
                    "chunkIndexes": []
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
                    "reason": "Error fallback",
                    "answerMatchType": "Error",
                    "chunkIndexes": []
                }
                writer({"event": "answer_chunk", "data": {"chunk": error_content}})
                writer({"event": "complete", "data": error_response})
                final_content = error_response

        # Store final response - just the answer text
        if final_content:
            state["response"] = final_content.get("answer", str(final_content))
            state["completion_data"] = final_content

        response_len = len(str(final_content))
        logger.debug(f"Generated new response: {response_len} chars")
        return state

    except Exception as e:
        logger.error(f"Error in agent final response: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        writer({"event": "error", "data": {"error": str(e)}})
        return state


# Helper function to normalize response
def _normalize_response_format(response) -> dict:
    """Normalize response to expected format - handle both string and dict responses"""
    if isinstance(response, str):
        # Try to parse if it looks like JSON
        if response.strip().startswith('{'):
            try:
                import json
                parsed = json.loads(response)
                if isinstance(parsed, dict) and "answer" in parsed:
                    return {
                        "answer": parsed.get("answer", ""),
                        "citations": parsed.get("citations", []),
                        "confidence": parsed.get("confidence", "High"),
                        "reason": parsed.get("reason", "Direct response"),
                        "answerMatchType": parsed.get("answerMatchType", "Derived From Tool Execution"),
                        "chunkIndexes": parsed.get("chunkIndexes", []),
                        "workflowSteps": parsed.get("workflowSteps", [])
                    }
            except Exception:
                pass

        # Plain string response
        return {
            "answer": response,
            "citations": [],
            "confidence": "High",
            "reason": "Direct response",
            "answerMatchType": "Direct Response",
            "chunkIndexes": [],
            "workflowSteps": []
        }

    elif isinstance(response, dict):
        # Already in dict format, ensure required keys exist
        return {
            "answer": response.get("answer", str(response.get("content", response))),
            "citations": response.get("citations", []),
            "confidence": response.get("confidence", "Medium"),
            "reason": response.get("reason", "Processed response"),
            "answerMatchType": response.get("answerMatchType", "Derived From Tool Execution"),
            "chunkIndexes": response.get("chunkIndexes", []),
            "workflowSteps": response.get("workflowSteps", [])
        }
    else:
        # Fallback for other types
        return {
            "answer": str(response),
            "citations": [],
            "confidence": "Low",
            "reason": "Converted response",
            "answerMatchType": "Direct Response",
            "chunkIndexes": [],
            "workflowSteps": []
        }


def _is_beautiful_markdown(text: str) -> bool:
    """Check if text is already beautifully formatted markdown"""
    if not text or not isinstance(text, str):
        return False

    # Check for markdown elements
    has_headers = any(line.startswith('#') for line in text.split('\n'))
    has_lists = any(line.strip().startswith(('-', '*', '1.', '2.', '3.')) for line in text.split('\n'))
    has_bold = '**' in text
    has_structure = '\n\n' in text  # Paragraph breaks

    return has_headers or (has_lists and has_bold) or (has_structure and len(text) > 100)


def _beautify_markdown(text: str) -> str:
    """Transform plain text into beautiful markdown"""
    if not text:
        return text

    # If it's JSON, parse and format
    if text.strip().startswith('{'):
        try:
            data = json.loads(text)
            return _format_dict_as_markdown(data)
        except Exception:
            pass

    # Basic beautification
    lines = text.split('\n')
    formatted_lines = []

    for line in lines:
        line = line.strip()
        if not line:
            formatted_lines.append('')
            continue

        # Add basic formatting
        if line.endswith(':') and len(line) < 50:
            # Likely a header
            formatted_lines.append(f"## {line[:-1]}")
        elif line.startswith('-') or line.startswith('*'):
            # Already a list
            formatted_lines.append(line)
        else:
            formatted_lines.append(line)

    return '\n'.join(formatted_lines)


def _format_dict_as_markdown(data: dict) -> str:
    """Format a dictionary as beautiful markdown"""
    lines = ["# Response\n"]

    for key, value in data.items():
        if key in ['status', 'error', 'message']:
            continue

        # Format key as header
        formatted_key = key.replace('_', ' ').title()
        lines.append(f"## {formatted_key}\n")

        if isinstance(value, dict):
            for k, v in value.items():
                lines.append(f"- **{k}**: {v}")
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    lines.append(f"- {json.dumps(item, indent=2)}")
                else:
                    lines.append(f"- {item}")
        else:
            lines.append(str(value))

        lines.append("")

    return '\n'.join(lines)


def _build_workflow_summary(tool_results):
    """Build a summary of the workflow steps"""
    steps = []
    for idx, result in enumerate(tool_results, 1):
        tool_name = result.get("tool_name", "unknown")
        status = result.get("status", "unknown")

        step_desc = f"{idx}. {tool_name}"
        if status == "success":
            step_desc += " ✅"
        else:
            step_desc += " ❌"

        steps.append(step_desc)

    return steps


async def _stream_structured_response(content, writer, logger):
    """Stream structured response with beautiful markdown answer"""
    answer_text = content.get("answer", "")
    chunk_size = 50

    # Stream the answer in chunks
    for i in range(0, len(answer_text), chunk_size):
        chunk = answer_text[i:i + chunk_size]
        writer({"event": "answer_chunk", "data": {"chunk": chunk}})
        await asyncio.sleep(0.01)

    # Send complete structured data
    writer({"event": "complete", "data": content})
    logger.debug(f"✅ Streamed structured response: {len(answer_text)} chars with citations")


async def _stream_conversational_response(content, writer, logger):
    """Stream conversational markdown response"""
    answer_text = content.get("answer", str(content))
    chunk_size = 50

    # Stream in chunks
    for i in range(0, len(answer_text), chunk_size):
        chunk = answer_text[i:i + chunk_size]
        writer({"event": "answer_chunk", "data": {"chunk": chunk}})
        await asyncio.sleep(0.01)

    # Send complete event with proper format
    complete_data = {
        "answer": answer_text,
        "citations": [],
        "confidence": "High",
        "reason": "Markdown response (no internal knowledge cited)"
    }
    writer({"event": "complete", "data": complete_data})
    logger.debug(f"✅ Streamed markdown response: {len(answer_text)} chars")


def _prepare_final_messages(state, has_internal_knowledge):
    """Prepare messages for final response generation"""
    validated_messages = []

    for msg in state.get("messages", []):
        if isinstance(msg, SystemMessage):
            validated_messages.append({"role": "system", "content": msg.content})
        elif isinstance(msg, HumanMessage):
            validated_messages.append({"role": "user", "content": msg.content})
        elif isinstance(msg, AIMessage):
            validated_messages.append({"role": "assistant", "content": msg.content})

    # Add tool summary and output format reminder
    if state.get("all_tool_results"):
        from app.modules.agents.qna.tool_registry import get_tool_results_summary
        tool_summary = get_tool_results_summary(state)

        summary_message = f"\n\n## Complete Workflow Summary\n{tool_summary}"

        # Add format reminder
        if has_internal_knowledge:
            summary_message += "\n\n⚠️ **CRITICAL**: You have internal knowledge sources. Your response MUST be in Structured JSON format with citations [1][2][3]. The 'answer' field should contain beautifully formatted Markdown."
        else:
            summary_message += "\n\n💡 **Output Format**: Respond in Beautiful Markdown format (no internal knowledge to cite)."

        summary_message += "\n\nProvide a comprehensive final response based on all information gathered."

        if validated_messages and validated_messages[-1]["role"] == "user":
            validated_messages[-1]["content"] += summary_message
        else:
            validated_messages.append({
                "role": "user",
                "content": summary_message
            })
    else:
        # No tools used, just add format reminder
        format_reminder = ""
        if has_internal_knowledge:
            format_reminder = "\n\n⚠️ **Remember**: Respond in Structured JSON with citations since you have internal knowledge."
        else:
            format_reminder = "\n\n💡 **Output**: Use Beautiful Markdown format."

        if validated_messages and validated_messages[-1]["role"] == "user":
            validated_messages[-1]["content"] += format_reminder

    return validated_messages


async def _generate_streaming_response(llm, messages, final_results, writer, logger, state):
    """Generate response with streaming and proper format"""
    try:
        writer({"event": "status", "data": {"status": "generating", "message": "✍️ Creating response..."}})

        has_internal_knowledge = state.get("has_internal_knowledge", False)
        final_content = None

        async for stream_event in stream_llm_response(llm, messages, final_results, logger):
            event_type = stream_event["event"]
            event_data = stream_event["data"]

            writer({"event": event_type, "data": event_data})

            if event_type == "complete":
                final_content = event_data

                # Ensure beautiful formatting
                if isinstance(final_content, dict) and "answer" in final_content:
                    if not _is_beautiful_markdown(final_content["answer"]):
                        logger.warning("Beautifying answer...")
                        final_content["answer"] = _beautify_markdown(final_content["answer"])

                # Add workflow steps if complex
                if state.get("requires_planning") and state.get("all_tool_results"):
                    if isinstance(final_content, dict):
                        final_content["workflowSteps"] = _build_workflow_summary(state["all_tool_results"])

        return final_content

    except Exception as stream_error:
        logger.error(f"Streaming failed: {stream_error}")

        # Fallback
        response = await llm.ainvoke(messages)
        content = response.content if hasattr(response, 'content') else str(response)

        # Process based on mode
        if has_internal_knowledge and final_results:
            cited_content = process_citations(content, final_results, [], from_agent=True)
            if isinstance(cited_content, str):
                content = cited_content
            elif isinstance(cited_content, dict):
                content = cited_content.get("answer", content)

        # Beautify if needed
        if not _is_beautiful_markdown(content):
            content = _beautify_markdown(content)

        # Stream fallback
        chunk_size = 100
        for i in range(0, len(content), chunk_size):
            chunk = content[i:i + chunk_size]
            writer({"event": "answer_chunk", "data": {"chunk": chunk}})
            await asyncio.sleep(0.02)

        # Build completion data
        if has_internal_knowledge and final_results:
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
                "answer": content,
                "citations": citations,
                "confidence": "Medium",
                "reason": "Fallback response with internal knowledge"
            }
        else:
            completion_data = {
                "answer": content,
                "citations": [],
                "confidence": "Medium",
                "reason": "Fallback markdown response"
            }

        # Add workflow if available
        if state.get("all_tool_results"):
            completion_data["workflowSteps"] = _build_workflow_summary(state["all_tool_results"])

        writer({"event": "complete", "data": completion_data})

        return completion_data

def _clean_response(response):
    """Clean response format"""
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


def _validate_and_fix_message_sequence(messages):
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

    if pending_tool_calls:
        final_validated = []
        for msg in validated:
            if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
                has_unresolved = any(
                    (tc.get('id') if isinstance(tc, dict) else getattr(tc, 'id', None)) in pending_tool_calls
                    for tc in msg.tool_calls
                )
                if not has_unresolved:
                    final_validated.append(msg)
            else:
                final_validated.append(msg)
        validated = final_validated

    return validated


def _clean_message_history(messages):
    """Clean message history"""
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
                        tool_call_ids = [
                            tc.get('id') if isinstance(tc, dict) else getattr(tc, 'id', None)
                            for tc in prev_msg.tool_calls
                        ]

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


# ============================================================================
# ROUTING FUNCTIONS
# ============================================================================

def should_continue(state: ChatState) -> Literal["execute_tools", "final"]:
    """Route based on tool calls"""
    return "execute_tools" if state.get("pending_tool_calls", False) else "final"


def check_for_error(state: ChatState) -> Literal["error", "continue"]:
    """Check for errors"""
    return "error" if state.get("error") else "continue"
