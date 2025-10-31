import asyncio
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import StreamWriter

from app.modules.agents.qna.chat_state import ChatState
from app.modules.qna.agent_prompt import (
    create_agent_messages,
    detect_response_mode,
)
from app.utils.citations import fix_json_string, process_citations
from app.utils.streaming import stream_llm_response

# Constants
RESULT_PREVIEW_LENGTH = 150
MARKDOWN_MIN_LENGTH = 100
HEADER_LENGTH_THRESHOLD = 50
STREAMING_CHUNK_DELAY = 0.01  # Delay for streaming effect
STREAMING_FALLBACK_DELAY = 0.02  # Delay for fallback streaming

# Lint-related thresholds (replaces magic numbers)
TUPLE_RESULT_LEN = 2
SHORT_ERROR_TEXT_THRESHOLD = 100
RECENT_CALLS_WINDOW = 5
REPETITION_MIN_COUNT = 2
JSON_RICH_OBJECT_MIN_KEYS = 3
KEY_VALUE_PATTERN_MIN_COUNT = 3
RESULT_PREVIEW_MAX_LEN = 200
RESULT_STR_LONG_THRESHOLD = 1000
ID_VALUE_MIN_LENGTH = 10
MAX_RETRIES_PER_TOOL = 2
REPEATED_SUCCESS_MIN_COUNT = 2
COMPREHENSIVE_SUCCESS_MIN = 3
COMPREHENSIVE_TYPES_MIN = 2
PARTIAL_SUCCESS_MIN = 2
PARTIAL_DATA_MIN = 2
RECENT_FAILURE_WINDOW = 3
PING_REPEAT_MIN = 3
SUSPICIOUS_RESPONSE_MIN = 100

# Context Management Constants
LOOP_DETECTION_MIN_CALLS = 5
LOOP_DETECTION_MAX_UNIQUE_TOOLS = 2
MAX_ITERATION_COUNT = 15
MAX_CONTEXT_CHARS = 100000  # Rough estimate: 100k chars ≈ 25k tokens
MAX_MESSAGES_HISTORY = 20
MAX_TOOL_RESULT_LENGTH = 2000
MAX_TOOLS_PER_ITERATION = 5

# ============================================================================
# GENERIC TOOL RESULT ANALYSIS FUNCTIONS
# ============================================================================

def _detect_tool_success(result: object) -> bool:
    """
    Properly detect if a tool execution was successful.
    Handles JSON responses, tuples, and string responses.

    Args:
        result: Tool execution result
    Returns:
        True if successful, False otherwise
    """
    # Handle tuple format (success, data)
    if isinstance(result, tuple) and len(result) == TUPLE_RESULT_LEN:
        success_flag, _ = result
        return bool(success_flag)

    # Convert to string for analysis
    result_str = str(result)

    # Try to parse as JSON for more accurate detection
    try:
        if result_str.strip().startswith('{'):
            import json
            data = json.loads(result_str)

            # Check for explicit error indicators
            if isinstance(data, dict):
                # Check for error field
                if "error" in data:
                    return False

                # Check for status field
                if "status" in data:
                    status = str(data["status"]).lower()
                    if status in ["error", "failed", "failure", "400", "500", "503"]:
                        return False
                    if status in ["success", "ok", "200", "201"]:
                        return True

                # Check for success field (handle both boolean and string)
                if "success" in data:
                    success_value = data["success"]
                    # Handle boolean
                    if isinstance(success_value, bool):
                        return success_value
                    # Handle string "true"/"false"
                    if isinstance(success_value, str):
                        return success_value.lower() in ["true", "success"]
                    return bool(success_value)

                # Check for object: "error" (Notion/API error responses)
                if data.get("object") == "error":
                    return False

                # Check for message field with success indicators
                message = str(data.get("message", "")).lower()
                if any(word in message for word in ["success", "created", "updated", "completed"]):
                    # But double-check there's no error data
                    if "data" in data and isinstance(data["data"], dict):
                        if data["data"].get("object") == "error":
                            return False
                    return True

                # If we have "data" field with error object, it's a failure
                if "data" in data and isinstance(data["data"], dict):
                    if data["data"].get("object") == "error":
                        return False
    except Exception:
        pass

    # Fallback to string analysis (case-insensitive)
    result_lower = result_str.lower()

    # Check for explicit error indicators
    error_indicators = [
        "error:",
        '"error"',
        "'error'",
        "failed",
        "failure",
        "exception",
        "traceback",
        '"object": "error"',
        "'object': 'error'",
        "status_code: 400",
        "status_code: 500",
        "http/1.1 400",
        "http/1.1 500"
    ]

    if any(indicator in result_lower for indicator in error_indicators):
        return False

    # If result is very short and contains only "error" or similar, it's a failure
    if len(result_str.strip()) < SHORT_ERROR_TEXT_THRESHOLD and any(word in result_lower for word in ["error", "failed", "exception"]):
        return False

    # Default to success if no clear error indicators
    return True


def analyze_tool_results_generic(all_tool_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Generic analysis of tool results to provide intelligent context to the LLM.
    This replaces hardcoded tool-specific logic with dynamic analysis.
    """
    if not all_tool_results:
        return {
            "summary": "No tools have been executed yet.",
            "data_available": {},
            "repetition_warnings": [],
            "successful_tools": [],
            "failed_tools": []
        }

    # Analyze tool execution patterns
    tool_counts = {}
    successful_tools = []
    failed_tools = []
    data_available = {}
    repetition_warnings = []

    for result in all_tool_results:
        tool_name = result.get("tool_name", "unknown")
        status = result.get("status", "unknown")
        tool_result = result.get("result", "")

        # Count tool usage
        tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1

        # Categorize by success/failure
        if status == "success":
            successful_tools.append(tool_name)
            # Analyze what data is available from successful tools
            data_available[tool_name] = analyze_tool_data_content(tool_name, tool_result)
        else:
            failed_tools.append(tool_name)

    # Detect repetition patterns
    for tool_name, count in tool_counts.items():
        if count >= REPETITION_MIN_COUNT:
            recent_calls = [r for r in all_tool_results[-RECENT_CALLS_WINDOW:] if r.get("tool_name") == tool_name]
            if len(recent_calls) >= REPETITION_MIN_COUNT:
                repetition_warnings.append(f"{tool_name} has been called {count} times recently")

    # Generate intelligent summary
    summary_parts = []
    if successful_tools:
        unique_successful = list(set(successful_tools))
        summary_parts.append(f"Successfully executed: {', '.join(unique_successful)}")

    if failed_tools:
        unique_failed = list(set(failed_tools))
        summary_parts.append(f"Failed executions: {', '.join(unique_failed)}")

    if repetition_warnings:
        summary_parts.append(f"Repetition warnings: {len(repetition_warnings)}")

    return {
        "summary": "; ".join(summary_parts) if summary_parts else "Tool execution completed",
        "data_available": data_available,
        "repetition_warnings": repetition_warnings,
        "successful_tools": list(set(successful_tools)),
        "failed_tools": list(set(failed_tools)),
        "tool_counts": tool_counts,
        "total_executions": len(all_tool_results)
    }


def analyze_tool_data_content(tool_name: str, tool_result: str) -> Dict[str, Any]:
    """
    Analyze tool result content to determine what data is available.
    **FULLY GENERIC** - works for ANY tool by analyzing structure and patterns.
    """
    result_str = str(tool_result)
    result_lower = result_str.lower()

    # Generic data type detection based on structure, not specific keywords
    data_types = []

    # Check for success indicators
    if any(indicator in result_lower for indicator in ["success", "completed", "created", "updated", "retrieved"]):
        data_types.append("successful_execution")

    # Detect structured data (JSON, dict, list)
    try:
        import json
        parsed = json.loads(result_str) if result_str.strip().startswith('{') or result_str.strip().startswith('[') else None
        if parsed:
            data_types.append("structured_data")
            # Analyze JSON structure generically
            if isinstance(parsed, dict):
                data_types.append("object_data")
                if len(parsed.keys()) > JSON_RICH_OBJECT_MIN_KEYS:
                    data_types.append("rich_object")
            elif isinstance(parsed, list):
                data_types.append("list_data")
                if len(parsed) > 0:
                    data_types.append("collection_data")
    except (json.JSONDecodeError, Exception):
        pass

    # Detect if result contains array/list patterns (even if not valid JSON)
    if '[' in result_str and ']' in result_str:
        data_types.append("list_pattern")

    # Detect if result contains multiple key-value patterns
    if result_str.count(':') > KEY_VALUE_PATTERN_MIN_COUNT or result_str.count('=') > KEY_VALUE_PATTERN_MIN_COUNT:
        data_types.append("key_value_data")

    # Detect URLs/links
    if 'http://' in result_lower or 'https://' in result_lower:
        data_types.append("contains_links")

    # Determine next possible actions based on tool name patterns (generic)
    next_actions = []
    tool_name_lower = tool_name.lower()

    # Retrieval/Read operations
    if any(verb in tool_name_lower for verb in ["fetch", "get", "retrieve", "list", "find", "search", "query"]):
        next_actions.append("use_retrieved_data")
        next_actions.append("provide_response")

    # Creation/Write operations
    if any(verb in tool_name_lower for verb in ["send", "create", "add", "post", "publish", "schedule"]):
        next_actions.append("verify_action_completed")
        next_actions.append("provide_confirmation")

    # Update/Modify operations
    if any(verb in tool_name_lower for verb in ["update", "edit", "modify", "change", "patch"]):
        next_actions.append("verify_changes")
        next_actions.append("provide_update_confirmation")

    # Delete operations
    if any(verb in tool_name_lower for verb in ["delete", "remove", "cancel", "archive"]):
        next_actions.append("confirm_deletion")
        next_actions.append("provide_status")

    return {
        "data_types": data_types,
        "next_actions": next_actions,
        "has_data": len(data_types) > 0,
        "result_preview": str(tool_result)[:RESULT_PREVIEW_MAX_LEN] + "..." if len(str(tool_result)) > RESULT_PREVIEW_MAX_LEN else str(tool_result)
    }


def get_tool_results_summary(tool_results: List[Dict[str, Any]]) -> str:
    """
    Simple summary of tool results for LLM context.
    """
    if not tool_results:
        return "No tools have been executed yet."

    summary_parts = [f"**Tool Execution Summary** ({len(tool_results)} tools executed):"]

    for i, result in enumerate(tool_results[-5:], 1):  # Last 5 results
        tool_name = result.get("tool_name", "unknown")
        tool_result = result.get("result", "")
        result_str = str(tool_result)

        actual_status = result.get("status", "unknown")
        if actual_status == "success":
            status = "✅ SUCCESS"
        elif actual_status == "error":
            status = "❌ FAILED"
        else:
            status = "⚠️ UNKNOWN"

        summary_parts.append(f"\n**Tool {i}: {tool_name}** - {status}")

        # Show actual result data (truncated)
        if len(result_str) > RESULT_STR_LONG_THRESHOLD:
            summary_parts.append(f"**Result**: {result_str[:RESULT_STR_LONG_THRESHOLD]}...")
        else:
            summary_parts.append(f"**Result**: {result_str}")

        # Add explicit error message if failed
        if actual_status == "error":
            summary_parts.append("**⚠️ This tool FAILED - do not retry it with the same parameters**")

    return "\n".join(summary_parts)


def _determine_query_intent(query_lower: str) -> str:
    """Determine the user's intent from their query."""
    if any(word in query_lower for word in ["list", "show", "get", "fetch", "find"]):
        return "data_retrieval"
    elif any(word in query_lower for word in ["send", "create", "add", "post"]):
        return "action_request"
    elif any(word in query_lower for word in ["who", "what", "when", "where", "how"]):
        return "information_query"
    else:
        return "general_query"


def build_simple_tool_context(state: ChatState) -> str:
    """
    Build explicit tool context that clearly shows what data the LLM has.
    """
    all_tool_results = state.get("all_tool_results", [])

    if not all_tool_results:
        return ""

    # Analyze what data we actually have - GENERIC analysis
    successful_tools = []
    failed_tools = []
    data_summary = {}

    for result in all_tool_results:
        tool_name = result.get("tool_name", "unknown")
        tool_result = result.get("result", "")
        result_str = str(tool_result)

        actual_status = result.get("status", "unknown")

        if actual_status == "success":
            successful_tools.append(tool_name)

            # **GENERIC** data analysis - works for ANY tool
            analysis = analyze_tool_data_content(tool_name, result_str)
            if analysis["has_data"]:
                # Store generic info about what kind of data is available
                data_types = analysis.get("data_types", [])
                if data_types:
                    data_summary[tool_name] = ", ".join(data_types[:3])  # Store top 3 data types
        else:
            failed_tools.append(tool_name)

    # Build explicit context
    context_parts = [
        "\n\n## 📊 TOOL EXECUTION SUMMARY",
        f"**Total Tools Executed**: {len(all_tool_results)}",
        f"**Successful Tools**: {len(successful_tools)}",
        f"**Failed Tools**: {len(failed_tools)}"
    ]

    # Show what data is available - GENERIC display
    if data_summary:
        context_parts.append("\n### ✅ DATA AVAILABLE:")
        for tool_name, data_info in data_summary.items():
            # Show tool name and what types of data it retrieved
            context_parts.append(f"- **{tool_name}**: {data_info}")

    # Show recent tool results with clear status
    context_parts.append("\n### 🔍 RECENT TOOL RESULTS:")
    for i, result in enumerate(all_tool_results[-5:], 1):
        tool_name = result.get("tool_name", "unknown")
        tool_result = result.get("result", "")
        result_str = str(tool_result)

        actual_status = result.get("status", "unknown")
        if actual_status == "success":
            status = "✅ SUCCESS"
        elif actual_status == "error":
            status = "❌ FAILED"
        else:
            status = "⚠️ UNKNOWN"

        context_parts.append(f"\n**Tool {i}: {tool_name}** - {status}")

        if actual_status == "success":
            import json
            import re

            extracted_info = []
            try:
                if result_str.strip().startswith('{'):
                    data = json.loads(result_str)

                    if isinstance(data, dict):
                        # Look in nested "data" object first, but also check root
                        data_obj = data.get("data", data)

                        if isinstance(data_obj, dict):
                            # Pattern 1: Any field ending with "_id" or named "id"
                            id_fields = []
                            name_fields = []
                            other_important = []

                            for key, value in data_obj.items():
                                if not value or not isinstance(value, (str, int, float)):
                                    continue

                                key_lower = key.lower()
                                value_str = str(value)

                                # Identify IDs by key pattern (any field with "id" in name)
                                if key_lower.endswith('_id') or key_lower == 'id' or 'id' in key_lower:
                                    # Only include if it looks like an ID (UUID, long string, etc.)
                                    if len(value_str) > ID_VALUE_MIN_LENGTH or '-' in value_str:
                                        id_fields.append(f"{key}: {value_str[:40]}")

                                # Identify names/titles by key pattern (common descriptive fields)
                                elif any(name_key in key_lower for name_key in ['name', 'title', 'label', 'summary', 'subject', 'topic']):
                                    name_fields.append(f"{key}: {value_str[:50]}")

                                # Identify other important fields by value pattern (URLs, codes, etc.)
                                elif any(pattern in value_str.lower() for pattern in ['http://', 'https://', '.com', '.org', 'meet.', 'zoom.']):
                                    other_important.append(f"{key}: {value_str[:60]}")

                                # Identify status/type/object fields
                                elif key_lower in ['status', 'type', 'object', 'kind', 'category']:
                                    other_important.append(f"{key}: {value_str}")

                            # Combine in priority order: IDs first, then names, then others
                            extracted_info.extend(id_fields[:2])  # Top 2 IDs
                            extracted_info.extend(name_fields[:1])  # Top 1 name
                            extracted_info.extend(other_important[:1])  # Top 1 other

            except (json.JSONDecodeError, Exception):
                # Fallback: use regex to extract ANY field with "id" pattern
                id_patterns = re.findall(r'"([^"]*id[^"]*?)"\s*:\s*"([^"]{10,})"', result_str, re.IGNORECASE)
                if id_patterns:
                    # Take first ID-like field found
                    key, value = id_patterns[0]
                    extracted_info.append(f"{key}: {value[:40]}")

            if extracted_info:
                context_parts.append(f"  🎯 **Key Data**: {' | '.join(extracted_info)}")

            # Show data types analysis
            analysis = analyze_tool_data_content(tool_name, result_str)
            if analysis["has_data"]:
                data_types = analysis.get("data_types", [])
                if data_types:
                    context_parts.append(f"  ℹ️ **Data Type**: {', '.join(data_types[:2])}")

        # Show truncated result (but expand limit for successful results with IDs)
        max_length = 800 if actual_status == "success" else 300
        if len(result_str) > max_length:
            context_parts.append(f"  📄 **Full Result**: {result_str[:max_length]}...")
        else:
            context_parts.append(f"  📄 **Full Result**: {result_str}")

        if actual_status == "error":
            # Parse error to provide specific guidance
            error_lower = result_str.lower()
            retry_guidance = []

            if "uuid" in error_lower or "valid uuid" in error_lower or "validation" in error_lower:
                retry_guidance.append("  💡 **FIX**: Use a valid UUID format (32 hex chars with dashes, e.g., 12345678-1234-1234-1234-123456789012)")
                retry_guidance.append("  🔍 **HINT**: Use the search tool first to get valid IDs, or ask the user")
            elif "permission" in error_lower or "insufficient" in error_lower or "403" in error_lower:
                retry_guidance.append("  ⚠️ **FIX**: This is a permission error - retrying won't help")
                retry_guidance.append("  📝 **ACTION**: Inform user they need to grant additional permissions")
            elif "not found" in error_lower or "404" in error_lower:
                retry_guidance.append("  💡 **FIX**: The resource doesn't exist - check the ID or name")
                retry_guidance.append("  🔍 **HINT**: Use search/list tools to find the correct resource")
            elif "authentication" in error_lower or "401" in error_lower:
                retry_guidance.append("  ⚠️ **FIX**: Authentication failed - retrying won't help")
                retry_guidance.append("  📝 **ACTION**: Inform user to re-connect the integration")
            else:
                retry_guidance.append("  💡 **FIX**: Read the error carefully and correct the parameters")

            if retry_guidance:
                context_parts.extend(retry_guidance)

    # Add explicit guidance
    context_parts.append("\n### 🎯 CRITICAL DECISION GUIDANCE:")

    if len(failed_tools) > 0:
        context_parts.append(f"\n⚠️ **{len(failed_tools)} TOOL(S) HAVE FAILED**")

        # Get retry count from state
        retry_count = state.get("tool_retry_count", {})

        # Track which tools have failed and show their errors
        failed_tool_details = {}
        for result in all_tool_results:
            if result.get("status") == "error":
                tool_name = result.get("tool_name", "unknown")
                if tool_name not in failed_tool_details:
                    failed_tool_details[tool_name] = {
                        "count": 0,
                        "error": str(result.get("result", ""))[:300],
                        "args": result.get("args", {})
                    }
                failed_tool_details[tool_name]["count"] += 1

        context_parts.append("\n**FAILED TOOLS - ANALYZE AND FIX:**")
        for tool_name, details in failed_tool_details.items():
            count = details["count"]
            error_preview = details["error"]
            args = details["args"]
            retries = retry_count.get(tool_name, 0)

            context_parts.append(f"\n❌ **{tool_name}** - Failed {count} time(s) (Retry #{retries})")
            context_parts.append(f"   📝 **Error**: {error_preview}")
            context_parts.append(f"   🔧 **Args Used**: {str(args)[:150]}")

            if retries >= MAX_RETRIES_PER_TOOL:
                context_parts.append("   🛑 **MAX RETRIES EXCEEDED** - Cannot retry again")
                context_parts.append("   📝 **ACTION**: Inform user about the failure and what went wrong")
            else:
                context_parts.append("   🔄 **CAN RETRY** - Analyze the error and fix the parameters")
                context_parts.append("   💡 **ACTION**: Correct the parameters based on error message and retry")

        if any(retry_count.get(tool, 0) < MAX_RETRIES_PER_TOOL for tool in failed_tool_details):
            context_parts.append("\n✅ **YOU CAN RETRY** - Fix the parameters and try again")
            context_parts.append("🔍 **HOW TO FIX**: Read error messages carefully, they tell you exactly what's wrong")
        else:
            context_parts.append("\n🛑 **MAX RETRIES EXCEEDED** - Cannot retry these tools")
            context_parts.append("📝 **ACTION**: Provide final response explaining what succeeded and what failed")

    if len(successful_tools) > 0:
        context_parts.append(f"\n✅ **You have successfully executed {len(successful_tools)} tool(s)**")

        from collections import Counter
        tool_counts = Counter(successful_tools)
        repeated_tools = {tool: count for tool, count in tool_counts.items() if count >= REPEATED_SUCCESS_MIN_COUNT}

        if repeated_tools:
            context_parts.append("\n🚨 **REPEATED TOOL CALLS DETECTED**:")
            for tool, count in repeated_tools.items():
                context_parts.append(f"   - **{tool}** called {count} times successfully")
            context_parts.append("\n⚠️ **WARNING**: You have already executed these tools multiple times!")
            context_parts.append("🛑 **STOP IMMEDIATELY**: Do NOT call these tools again")
            context_parts.append("📝 **ACTION**: Provide your final response summarizing what was created/retrieved")
            context_parts.append("❌ **DO NOT**: Continue calling the same tools - you will create duplicates")

        unique_tool_types = set([tool.split('.')[0] for tool in successful_tools])  # Count distinct tool categories
        data_richness_score = len(data_summary)  # How many tools returned rich data

        if len(successful_tools) >= COMPREHENSIVE_SUCCESS_MIN and data_richness_score >= COMPREHENSIVE_SUCCESS_MIN and len(unique_tool_types) >= COMPREHENSIVE_TYPES_MIN:
            context_parts.append("\n🎯 **COMPREHENSIVE DATA AVAILABLE**: Multiple successful tool executions with rich data")
            context_parts.append("🚨 **STOP**: You likely have enough data to answer the user's question")
            context_parts.append("📝 **ACTION**: Provide your final response using the available data")
            context_parts.append("⚠️ **DO NOT**: Call more tools unless absolutely necessary - avoid loops")
        elif len(successful_tools) >= PARTIAL_SUCCESS_MIN and data_richness_score >= PARTIAL_DATA_MIN:
            context_parts.append("\n📊 **PARTIAL DATA**: You have data from multiple sources")
            context_parts.append("🤔 **DECISION**: Consider if you need more data or can provide response with what you have")
        else:
            context_parts.append("\n📊 **SOME DATA**: You have successfully retrieved information")
            context_parts.append("🤔 **DECISION**: Consider if you need additional data or can proceed with your response")

    if len(successful_tools) == 0 and len(failed_tools) > 0:
        context_parts.append("\n❌ **ALL TOOLS FAILED**: No successful tool executions")
        context_parts.append("🚨 **CRITICAL**: Stop calling tools - they are not working")
        context_parts.append("📝 **ACTION**: Provide a response explaining what you attempted and what failed")
        context_parts.append("💡 **SUGGESTION**: Inform the user about the errors and suggest alternative approaches")

    from app.modules.agents.qna.tool_registry import _get_recently_failed_tools
    blocked_tools = _get_recently_failed_tools(state, None)

    if blocked_tools:
        context_parts.append(f"\n### 🚫 BLOCKED TOOLS ({len(blocked_tools)} tools unavailable):")
        context_parts.append("The following tools have been automatically removed from your available tools due to repeated failures:")
        for tool_name, count in blocked_tools.items():
            context_parts.append(f"- **{tool_name}** (failed {count} times)")
        context_parts.append("\n⚠️ **These tools are NOT available for selection** - they have been filtered out to prevent infinite loops")
        context_parts.append("✅ **Use different tools** or provide a response based on available data")

    context_parts.append("\n**REMEMBER**: ")
    context_parts.append("- Review failed tools and their errors carefully")
    context_parts.append("- Do NOT retry tools that have already failed")
    context_parts.append("- If tools are failing, provide a response about the failures")
    context_parts.append("- Use successful data when available, acknowledge failures when necessary")

    return "\n".join(context_parts)


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
                needs_internal_data = not re.search(r'\s*\[\d+\]', last_response)  # More robustly check for citations
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

        # Clean up retrieval artifacts to reduce state pollution
        from app.modules.agents.qna.chat_state import cleanup_state_after_retrieval
        cleanup_state_after_retrieval(state)
        logger.debug("Cleaned up retrieval artifacts to reduce state pollution")

        return state

    except Exception as e:
        logger.error(f"Error in retrieval: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state


# ============================================================================
# PHASE 3: USER CONTEXT
# ============================================================================

async def get_user_info_node(state: ChatState) -> ChatState:
    """User info is now populated at router level - this is a no-op"""
    try:
        logger = state["logger"]

        # User and org info are already populated in the initial state
        # This node is kept for compatibility but doesn't need to do anything
        logger.debug("User and org info already populated at router level")

        return state
    except Exception as e:
        logger.error(f"Error in get_user_info_node: {str(e)}", exc_info=True)
        return state


# ============================================================================
# PHASE 4: ENHANCED AGENT PROMPT PREPARATION
# ============================================================================

def prepare_agent_prompt_node(state: ChatState, writer: StreamWriter) -> ChatState:
    """Prepare enhanced agent prompt with dual-mode formatting instructions and user context"""
    try:
        logger = state["logger"]
        if state.get("error"):
            return state

        logger.debug("🎯 Preparing agent prompt with dual-mode support and user context")

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

        # Log user context availability
        user_info = state.get("user_info")
        org_info = state.get("org_info")
        if user_info and org_info:
            logger.info(f"👤 User context available: {user_info.get('userEmail', 'N/A')} ({org_info.get('accountType', 'N/A')})")
        else:
            logger.warning("⚠️ No user context available")

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

        # Check if we have comprehensive data and should stop
        if state.get("all_tool_results"):
            all_tool_results = state["all_tool_results"]

            # Count successful/failed tools and expose to state for agent awareness
            successful_count = sum(1 for r in all_tool_results if r.get("status") == "success")
            failed_count = sum(1 for r in all_tool_results if r.get("status") == "error")
            # Make counts available to downstream prompt/context builders
            state["successful_tool_count"] = successful_count
            state["failed_tool_count"] = failed_count
            if logger:
                logger.debug(f"Tool counts → success: {successful_count}, failed: {failed_count}")
            unique_tool_categories = set([r.get("tool_name", "").split('.')[0] for r in all_tool_results if r.get("status") == "success"])

            # Check recent failures (last N tool calls)
            recent_tool_results = all_tool_results[-RECENT_FAILURE_WINDOW:] if len(all_tool_results) >= RECENT_FAILURE_WINDOW else all_tool_results
            recent_failures = sum(1 for r in recent_tool_results if r.get("status") == "error")

            # Track whether retries are allowed
            allow_retry = False
            if recent_failures > 0:
                # Check retry count for the failed tool
                failed_tool_names = [r.get("tool_name") for r in recent_tool_results if r.get("status") == "error"]

                # Count how many times each tool has failed
                retry_count = state.get("tool_retry_count", {})
                max_retries_exceeded = False

                for tool_name in failed_tool_names:
                    tool_failures = retry_count.get(tool_name, 0)
                    if tool_failures >= MAX_RETRIES_PER_TOOL:  # Max retries per tool
                        max_retries_exceeded = True
                        logger.warning(f"⚠️ Tool {tool_name} has failed {tool_failures} times - max retries exceeded")

                if not max_retries_exceeded:
                    logger.info(f"🔄 Recent failures detected ({recent_failures}) - allowing retry with LLM feedback")
                    allow_retry = True
                    # Don't set force_final_response yet - let agent continue
                else:
                    logger.warning("🛑 Max retries exceeded for failed tools - forcing final response")
                    state["force_final_response"] = True
                    state["loop_reason"] = "Max retries exceeded for failed tools"
                    return state

            # Heuristic: If we have many successful tools from multiple categories AND no recent failures (or retries not allowed), likely comprehensive
            if successful_count >= COMPREHENSIVE_SUCCESS_MIN and len(unique_tool_categories) >= COMPREHENSIVE_TYPES_MIN and not allow_retry:
                logger.info(f"🎯 COMPREHENSIVE DATA DETECTED: {successful_count} successful tools from {len(unique_tool_categories)} categories")
                logger.info("🛑 Preventing further tool calls to avoid loops")
                state["force_final_response"] = True
                state["loop_detected"] = False
                state["loop_reason"] = f"Comprehensive data available - {successful_count} successful tool executions from multiple categories"
                return state
            elif allow_retry:
                logger.info(f"✅ Allowing agent to continue despite comprehensive data - retry needed for {recent_failures} failure(s)")
                # Don't set force_final_response - allow agent to run and create retry tool calls

        # Generic and robust loop prevention
        recent_tool_calls = state.get("all_tool_results", [])[-5:]  # Last 5 tool calls
        if len(recent_tool_calls) >= PING_REPEAT_MIN:  # Check after just N calls
            tool_names = [result.get("tool_name", "") for result in recent_tool_calls]
            # unique_tools = set(tool_names)

            # If same tool called 3 times in a row, force final response
            # if len(unique_tools) == 1:
            #     logger.warning(f"⚠️ LOOP DETECTED: {tool_names[0]} called 3 times consecutively")
            #     logger.warning("🛑 Forcing final response to prevent infinite loop")
            #     state["force_final_response"] = True
            #     state["loop_detected"] = True
            #     state["loop_reason"] = f"Loop detected - {tool_names[0]} called 3 times consecutively"
            #     return state

        # Check for longer patterns and tool repetition
        # if len(recent_tool_calls) >= LOOP_DETECTION_MIN_CALLS:
        #     tool_names = [result.get("tool_name", "") for result in recent_tool_calls]
        #     if len(set(tool_names)) <= LOOP_DETECTION_MAX_UNIQUE_TOOLS and len(tool_names) >= LOOP_DETECTION_MIN_CALLS:
        #         logger.warning(f"⚠️ Loop detected: {tool_names[-LOOP_DETECTION_MIN_CALLS:]} - forcing final response")
        #         state["force_final_response"] = True
        #         state["loop_detected"] = True
        #         state["loop_reason"] = "Loop detected - too many repeated tool calls"
        #         return state

        # Context length check
        if iteration_count > MAX_ITERATION_COUNT:
            logger.warning(f"⚠️ High iteration count ({iteration_count}) - forcing termination")
            state["error"] = {"status_code": 400, "detail": "Too many iterations - context may be too large"}
            return state

        # Status messages
        if iteration_count == 0 and is_complex:
            writer({"event": "status", "data": {"status": "planning", "message": "Creating execution plan..."}})
        elif iteration_count > 0:
            # Enhanced status with progress tracking
            recent_tools = [result.get("tool_name", "unknown") for result in state.get("all_tool_results", [])[-3:]]
            unique_recent = set(recent_tools)

            if len(unique_recent) == 1 and len(recent_tools) >= PING_REPEAT_MIN:
                writer({"event": "status", "data": {"status": "adapting", "message": f"⚠️ Avoiding repetition - adapting plan (step {iteration_count + 1})..."}})
            else:
                writer({"event": "status", "data": {"status": "adapting", "message": f"Adapting plan (step {iteration_count + 1})..."}})
        else:
            writer({"event": "status", "data": {"status": "thinking", "message": "Processing your request..."}})

        # Get tools
        from app.modules.agents.qna.tool_registry import get_agent_tools
        tools = get_agent_tools(state)

        if tools:
            logger.debug(f"Agent has {len(tools)} tools available")
            try:
                llm_with_tools = llm.bind_tools(tools)
            except (NotImplementedError, AttributeError) as e:
                logger.warning(f"LLM does not support tool binding: {e}")
                llm_with_tools = llm
                tools = []
        else:
            llm_with_tools = llm

        # Add simple tool context so LLM can see what tools have been executed
        if state.get("all_tool_results"):
            tool_context = build_simple_tool_context(state)

            # Add output format reminder
            if has_internal_knowledge:
                tool_context += "\n\n **Remember**: You have internal knowledge sources available. If you used them, respond in Structured JSON with citations."
            else:
                tool_context += "\n\n **Output**: Use Beautiful Markdown format for your final response."

            if state["messages"] and isinstance(state["messages"][-1], HumanMessage):
                state["messages"][-1].content += tool_context

        # Clean messages
        cleaned_messages = _clean_message_history(state["messages"])

        # Check context length before LLM call
        total_chars = sum(len(str(msg.content)) for msg in cleaned_messages if hasattr(msg, 'content'))
        if total_chars > MAX_CONTEXT_CHARS:  # Rough estimate: 100k chars ≈ 25k tokens
            logger.warning(f"⚠️ Context too large ({total_chars} chars) - truncating further")
            # Keep only the most recent messages
            cleaned_messages = cleaned_messages[:10]  # Keep only last 10 messages
            logger.info(f"Truncated to {len(cleaned_messages)} messages")

        # Simple debug logging
        if state.get("all_tool_results"):
            logger.debug(f"🔍 Agent context includes {len(state['all_tool_results'])} tool results")

            # Log recent tool results with accurate status
            recent_results = state.get("all_tool_results", [])[-3:]
            for i, result in enumerate(recent_results, 1):
                tool_name = result.get("tool_name", "unknown")
                tool_result = result.get("result", "")
                result_str = str(tool_result)

                # Use actual status field
                actual_status = result.get("status", "unknown")

                result_preview = result_str[:100]
                logger.info(f"🔍 Tool {i}: {tool_name} ({actual_status}) - Preview: {result_preview}...")

        # Call LLM
        logger.debug(f" Invoking LLM (iteration {iteration_count})")
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

def _detect_tool_success(result: object) -> bool:
    """
    Detect if a tool execution was successful.
    Simple and maintainable - all tools return {"success": true/false, ...} or {"error": "..."}

    Args:
        result: Tool execution result

    Returns:
        True if successful, False otherwise
    """
    # Handle tuple format (success_flag, data)
    if isinstance(result, tuple) and len(result) == TUPLE_RESULT_LEN:
        return bool(result[0])

    # Parse JSON and check for success/error indicators
    try:
        import json
        result_str = str(result)

        if result_str.strip().startswith('{'):
            data = json.loads(result_str)

            if isinstance(data, dict):
                # Check for "success" field first
                if "success" in data:
                    return bool(data["success"])

                # Check for "error" field with actual error content
                if "error" in data:
                    error_value = data["error"]
                    # If error has content (not None/null), it's a failure
                    if error_value and error_value != "null":
                        return False

    except Exception:
        pass

    # Fallback: if it starts with "Error:" it's a failure
    result_str = str(result)
    if result_str.startswith("Error:") or result_str.startswith("Error executing"):
        return False

    # Default: assume success
    return True


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

        # Limit tool calls per iteration
        if len(tool_calls) > MAX_TOOLS_PER_ITERATION:
            logger.warning(f"⚠️ Too many tool calls ({len(tool_calls)}) - limiting to {MAX_TOOLS_PER_ITERATION}")
            tool_calls = tool_calls[:MAX_TOOLS_PER_ITERATION]

        # Get available tools
        from app.modules.agents.qna.tool_registry import get_agent_tools
        tools = get_agent_tools(state)
        tools_by_name = {tool.name: tool for tool in tools}

        tool_messages = []
        tool_results = []

        for tool_call in tool_calls:
            tool_name = tool_call.get("name") if isinstance(tool_call, dict) else tool_call.name

            # Handle both tool_call.args and tool_call.function formats
            if isinstance(tool_call, dict):
                tool_args = tool_call.get("args", {})
                # Check for function format (used by some LLM providers like OpenAI)
                if not tool_args and "function" in tool_call:
                    function_data = tool_call["function"]
                    tool_args = function_data.get("arguments", {})
                    if isinstance(tool_args, str):
                        try:
                            tool_args = json.loads(tool_args)
                        except json.JSONDecodeError:
                            tool_args = {}
                tool_id = tool_call.get("id")
            else:
                tool_args = tool_call.args
                tool_id = tool_call.id

            try:
                result = None

                if tool_name in tools_by_name:
                    tool = tools_by_name[tool_name]
                    logger.info(f"▶️ Executing: {tool_name}")
                    logger.debug(f"  Args: {tool_args}")

                    result = tool._run(**tool_args) if hasattr(tool, '_run') else tool.run(**tool_args)

                    # Log result preview
                    result_preview = str(result)[:RESULT_PREVIEW_LENGTH] + "..." if len(str(result)) > RESULT_PREVIEW_LENGTH else str(result)
                    logger.debug(f"  Result preview: {result_preview}")
                else:
                    logger.warning(f"Tool not found: {tool_name}")
                    result = json.dumps({
                        "status": "error",
                        "message": f"Tool '{tool_name}' not found in registry",
                        "available_tools": list(tools_by_name.keys())
                    })

                # Properly detect tool success/failure
                is_success = _detect_tool_success(result)
                status = "success" if is_success else "error"

                # **DEBUG**: Log detection result
                logger.debug(f"🔍 Tool result detection: is_success={is_success}, status={status}")

                tool_result = {
                    "tool_name": tool_name,
                    "result": result,
                    "status": status,
                    "tool_id": tool_id,
                    "args": tool_args,
                    "execution_timestamp": datetime.now().isoformat(),
                    "iteration": iteration
                }
                tool_results.append(tool_result)

                tool_message = ToolMessage(content=str(result), tool_call_id=tool_id)
                tool_messages.append(tool_message)

                # Log correct status
                if is_success:
                    logger.info(f"✅ {tool_name} executed successfully")

                    # **GENERIC FEEDBACK**: Provide intelligent guidance based on tool result analysis
                    data_analysis = analyze_tool_data_content(tool_name, str(result))
                    if data_analysis["has_data"]:
                        logger.info(f"📊 {tool_name} retrieved data: {', '.join(data_analysis['data_types'])}")
                        if data_analysis["next_actions"]:
                            logger.info(f"🎯 Suggested next actions: {', '.join(data_analysis['next_actions'])}")
                else:
                    logger.error(f"❌ {tool_name} failed with error")
                    logger.error(f"Error details: {str(result)[:500]}")

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

        # **NEW**: Track retry count for failed tools
        if "tool_retry_count" not in state:
            state["tool_retry_count"] = {}

        for result in tool_results:
            if result.get("status") == "error":
                tool_name = result.get("tool_name")
                current_count = state["tool_retry_count"].get(tool_name, 0)
                state["tool_retry_count"][tool_name] = current_count + 1
                logger.warning(f"🔄 Tool {tool_name} retry count: {state['tool_retry_count'][tool_name]}")

        # Clean up old tool results to prevent memory pollution
        from app.modules.agents.qna.chat_state import cleanup_old_tool_results
        cleanup_old_tool_results(state, keep_last_n=15)  # Keep last 15 tool results

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
            error = state["error"]
            error_message = error.get("message", error.get("detail", "An error occurred"))

            # Format error as a proper completion response for frontend
            error_content = f"I apologize, but I encountered an issue: {error_message}"
            error_response = {
                "answer": error_content,
                "citations": [],
                "confidence": "Low",
                "reason": "Error occurred",
                "answerMatchType": "Error",
                "chunkIndexes": []
            }

            # Stream the error message
            writer({"event": "answer_chunk", "data": {"chunk": error_content}})

            # Send complete event with error response
            writer({"event": "complete", "data": error_response})

            # Store in state
            state["response"] = error_content
            state["completion_data"] = error_response

            logger.error(f"Formatted error response for frontend: {error_message}")
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

            # Stream only the answer text, not the JSON structure
            answer_text = final_content.get("answer", "")
            chunk_size = 50

            # Stream answer in chunks
            for i in range(0, len(answer_text), chunk_size):
                chunk = answer_text[i:i + chunk_size]
                writer({"event": "answer_chunk", "data": {"chunk": chunk}})
                await asyncio.sleep(STREAMING_CHUNK_DELAY)

            # Send complete structure only at the end
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
        # Clean message sequence first to ensure proper threading
        cleaned_messages = _clean_message_history(state.get("messages", []))

        validated_messages = []
        for msg in cleaned_messages:
            if isinstance(msg, SystemMessage):
                validated_messages.append({"role": "system", "content": msg.content})
            elif isinstance(msg, HumanMessage):
                validated_messages.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                # For AIMessage, preserve tool_calls ONLY if they're present and valid
                # (cleaned_messages should have already removed invalid tool_calls)
                msg_dict = {"role": "assistant", "content": msg.content}
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    msg_dict["tool_calls"] = msg.tool_calls
                validated_messages.append(msg_dict)
            elif isinstance(msg, ToolMessage):
                # Preserve tool_call_id for proper message threading
                validated_messages.append({
                    "role": "tool",
                    "content": msg.content,
                    "tool_call_id": msg.tool_call_id
                })

        # Add tool summary if available
        tool_context = ""
        if state.get("all_tool_results"):
            tool_context = build_simple_tool_context(state)

            # Add explicit instruction to synthesize ALL tool results
            tool_results_summary = get_tool_results_summary(state.get("all_tool_results", []))
            tool_context += f"\n\n{tool_results_summary}"

        # Analyze tool execution outcomes for proper user feedback
        all_tool_results = state.get("all_tool_results", [])
        iteration_count = len(all_tool_results)
        max_iterations = state.get("max_iterations", 30)

        # Count successes and failures
        successful_count = sum(1 for r in all_tool_results if r.get("status") == "success")
        failed_count = sum(1 for r in all_tool_results if r.get("status") == "error")

        comprehensive_context = ""
        force_comprehensive = False
        has_mixed_results = successful_count > 0 and failed_count > 0
        has_only_failures = failed_count > 0 and successful_count == 0

        # Check if comprehensive data was detected
        if state.get("force_final_response", False) and not state.get("loop_detected", False):
            loop_reason = state.get("loop_reason", "")
            if "Comprehensive data available" in loop_reason:
                force_comprehensive = True
                comprehensive_context = "\n\n🎯 **COMPREHENSIVE DATA DETECTED**: You have successfully executed multiple tools and gathered sufficient information. Please provide a DETAILED, COMPLETE response using ALL the available data to fully answer the user's question."

        # Check if max iterations reached - provide even stronger synthesis instruction
        if iteration_count >= max_iterations:
            force_comprehensive = True
            comprehensive_context += "\n\n⚠️ **IMPORTANT - MAX ITERATIONS REACHED**: You have executed many tool calls. Please now provide a comprehensive final answer that synthesizes ALL the information gathered from all tool executions. Don't provide a brief summary - give a detailed, complete response using ALL available data."
            logger.info(f"🛑 Max iterations reached ({iteration_count}) - forcing comprehensive synthesis")

        # Detect mixed results or failures - require detailed status report
        if has_mixed_results or has_only_failures or iteration_count > 0:
            force_comprehensive = True
            logger.info(f"📊 Tool execution summary: {successful_count} succeeded, {failed_count} failed")

        # Build full context with synthesis instructions if comprehensive mode
        if force_comprehensive:
            # Combine tool context and comprehensive context
            full_context = f"{tool_context}{comprehensive_context}"

            # Add explicit synthesis instruction
            synthesis_instruction = "\n\n## 📋 REQUIRED RESPONSE FORMAT\n\n"

            # Specific instructions based on execution outcomes
            if has_only_failures:
                synthesis_instruction += f"⚠️ **ALL TOOLS FAILED** ({failed_count} tool(s)):\n"
                synthesis_instruction += "- List EACH failed tool and WHY it failed (show actual error messages)\n"
                synthesis_instruction += "- Explain what was attempted and what went wrong\n"
                synthesis_instruction += "- Provide SPECIFIC next steps for the user:\n"
                synthesis_instruction += "  * What information is needed to retry successfully?\n"
                synthesis_instruction += "  * Alternative approaches they can take\n"
                synthesis_instruction += "  * What they should check or fix first\n"
                synthesis_instruction += "- Be helpful and constructive, not just report errors\n"
            elif has_mixed_results:
                synthesis_instruction += f"⚙️ **MIXED RESULTS** ({successful_count} succeeded, {failed_count} failed):\n"
                synthesis_instruction += "- Start with '## ✅ Successfully Completed' section:\n"
                synthesis_instruction += "  * List EACH successful action with details\n"
                synthesis_instruction += "  * Show what data was retrieved or what was created\n"
                synthesis_instruction += "  * Include relevant IDs, links, or references\n"
                synthesis_instruction += "- Then add '## ❌ Failed Actions' section:\n"
                synthesis_instruction += "  * List EACH failed tool and the specific error\n"
                synthesis_instruction += "  * Explain WHY each failure occurred\n"
                synthesis_instruction += "  * Provide SPECIFIC guidance on how to fix or retry\n"
                synthesis_instruction += "- End with '## 🎯 Next Steps' section:\n"
                synthesis_instruction += "  * What the user should do next\n"
                synthesis_instruction += "  * What information is still needed\n"
                synthesis_instruction += "  * Alternative approaches if needed\n"
            else:
                # All succeeded
                synthesis_instruction += f"✅ **ALL TOOLS SUCCEEDED** ({successful_count} tool(s)):\n"
                synthesis_instruction += "- Provide a comprehensive summary of what was accomplished\n"
                synthesis_instruction += "- Include all relevant data from successful tool executions\n"
                synthesis_instruction += "- Show specific results, IDs, links, or confirmations\n"
                synthesis_instruction += "- Format professionally with clear structure\n"

            synthesis_instruction += "\n**IMPORTANT**:\n"
            synthesis_instruction += f"- Review ALL {len(all_tool_results)} tool results shown above\n"
            synthesis_instruction += "- Provide a COMPLETE, DETAILED response (NOT just 1-2 sentences)\n"
            synthesis_instruction += "- Use proper markdown formatting with headers and lists\n"
            synthesis_instruction += "- Be specific - mention actual tool names, errors, and outcomes\n"
            synthesis_instruction += "- Help the user understand what happened and what to do next\n"
            synthesis_instruction += "\n**CRITICAL - JSON FORMAT REQUIRED**:\n"
            synthesis_instruction += "You MUST respond with ONLY a valid JSON object in this exact format:\n"
            synthesis_instruction += '{\n'
            synthesis_instruction += '  "answer": "Your detailed markdown response here",\n'
            synthesis_instruction += '  "citations": [],\n'
            synthesis_instruction += '  "confidence": "High",\n'
            synthesis_instruction += '  "reason": "Brief explanation",\n'
            synthesis_instruction += '  "answerMatchType": "Derived From Tool Execution"\n'
            synthesis_instruction += '}\n'
            synthesis_instruction += "DO NOT include any text before or after the JSON. Return ONLY the JSON object."

            full_context += synthesis_instruction

            # Debug logging
            logger.info(f"🎯 Final response context length: {len(full_context)} characters")
            logger.info(f"📊 Response type: {'Mixed results' if has_mixed_results else 'All failures' if has_only_failures else 'All succeeded'}")

            if validated_messages and validated_messages[-1]["role"] == "user":
                validated_messages[-1]["content"] += full_context
            else:
                validated_messages.append({
                    "role": "user",
                    "content": f"Based on the tool execution results:{full_context}\n\nPlease provide a comprehensive final response following the REQUIRED RESPONSE FORMAT above."
                })
        elif tool_context:
            # Normal case - just add tool context
            json_format_instruction = "\n\n**CRITICAL - JSON FORMAT REQUIRED**:\n"
            json_format_instruction += "You MUST respond with ONLY a valid JSON object in this exact format:\n"
            json_format_instruction += '{\n'
            json_format_instruction += '  "answer": "Your detailed response here",\n'
            json_format_instruction += '  "citations": [],\n'
            json_format_instruction += '  "confidence": "High",\n'
            json_format_instruction += '  "reason": "Brief explanation",\n'
            json_format_instruction += '  "answerMatchType": "Derived From Tool Execution"\n'
            json_format_instruction += '}\n'
            json_format_instruction += "DO NOT include any text before or after the JSON. Return ONLY the JSON object."

            if validated_messages and validated_messages[-1]["role"] == "user":
                validated_messages[-1]["content"] += f"\n\n{tool_context}{json_format_instruction}"
            else:
                validated_messages.append({
                    "role": "user",
                    "content": f"{tool_context}{json_format_instruction}\n\nPlease provide your response."
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

                # Forward streaming events as-is
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

                # Stream answer text only
                chunk_size = 100
                for i in range(0, len(fallback_content), chunk_size):
                    chunk = fallback_content[i:i + chunk_size]
                    writer({"event": "answer_chunk", "data": {"chunk": chunk}})
                    await asyncio.sleep(STREAMING_FALLBACK_DELAY)

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
            answer_text = final_content.get("answer", str(final_content))
            state["response"] = answer_text
            state["completion_data"] = final_content

            # Log response length and detect suspiciously short responses
            response_len = len(answer_text)
            logger.info(f"✅ Generated final response: {response_len} characters")

            if response_len < SUSPICIOUS_RESPONSE_MIN and len(all_tool_results) > 0:
                logger.error(f"⚠️ SUSPICIOUSLY SHORT RESPONSE ({response_len} chars) despite {len(all_tool_results)} tool executions!")
                logger.error(f"Response preview: {answer_text[:200]}")
                logger.error(f"Tool summary: {successful_count} succeeded, {failed_count} failed")
        else:
            logger.error("❌ No final content generated - this should not happen")

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

    return has_headers or (has_lists and has_bold) or (has_structure and len(text) > MARKDOWN_MIN_LENGTH)


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
        if line.endswith(':') and len(line) < HEADER_LENGTH_THRESHOLD:
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


def _build_workflow_summary(tool_results) -> List[str]:
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


async def _stream_structured_response(content, writer, logger) -> None:
    """Stream structured response with beautiful markdown answer"""
    answer_text = content.get("answer", "")
    chunk_size = 50

    # Stream the answer in chunks
    for i in range(0, len(answer_text), chunk_size):
        chunk = answer_text[i:i + chunk_size]
        writer({"event": "answer_chunk", "data": {"chunk": chunk}})
        await asyncio.sleep(STREAMING_CHUNK_DELAY)

    # Send complete structured data
    writer({"event": "complete", "data": content})
    logger.debug(f"✅ Streamed structured response: {len(answer_text)} chars with citations")


async def _stream_conversational_response(content, writer, logger) -> None:
    """Stream conversational markdown response"""
    answer_text = content.get("answer", str(content))
    chunk_size = 50

    # Stream in chunks
    for i in range(0, len(answer_text), chunk_size):
        chunk = answer_text[i:i + chunk_size]
        writer({"event": "answer_chunk", "data": {"chunk": chunk}})
        await asyncio.sleep(STREAMING_CHUNK_DELAY)

    # Send complete event with proper format
    complete_data = {
        "answer": answer_text,
        "citations": [],
        "confidence": "High",
        "reason": "Markdown response (no internal knowledge cited)"
    }
    writer({"event": "complete", "data": complete_data})
    logger.debug(f"✅ Streamed markdown response: {len(answer_text)} chars")


def _prepare_final_messages(state, has_internal_knowledge) -> List[Dict[str, str]]:
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


async def _generate_streaming_response(llm, messages, final_results, writer, logger, state) -> Optional[Dict[str, Any]]:
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
            await asyncio.sleep(STREAMING_FALLBACK_DELAY)

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

def _clean_response(response) -> Dict[str, Any]:
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


def _validate_and_fix_message_sequence(messages) -> List[Any]:
    """
    Validate and fix message sequence to ensure proper tool_call threading.

    OpenAI API Requirements:
    1. ToolMessages MUST have a preceding AIMessage with tool_calls
    2. AIMessages with tool_calls MUST have ALL corresponding ToolMessages
    3. Tool_call_id in ToolMessage MUST match id in AIMessage.tool_calls
    """
    validated = []
    pending_tool_calls = {}  # Maps tool_call_id -> True for expected tool responses

    # First pass: Build message sequence and track tool calls
    for msg in messages:
        if isinstance(msg, (SystemMessage, HumanMessage)):
            # Clear pending tool calls on new human message
            if isinstance(msg, HumanMessage):
                pending_tool_calls.clear()
            validated.append(msg)

        elif isinstance(msg, AIMessage):
            validated.append(msg)
            # Track tool calls
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_id = tc.get('id') if isinstance(tc, dict) else getattr(tc, 'id', None)
                    if tool_id:
                        pending_tool_calls[tool_id] = msg  # Store the AIMessage for reference

        elif hasattr(msg, 'tool_call_id'):
            # Only keep ToolMessage if it matches a pending tool call
            if msg.tool_call_id in pending_tool_calls:
                validated.append(msg)
                pending_tool_calls.pop(msg.tool_call_id, None)
            # else: drop orphaned ToolMessage

    # Second pass: Remove tool_calls from AIMessages that don't have responses
    if pending_tool_calls:
        final_validated = []
        for msg in validated:
            if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
                # Filter out tool_calls that don't have responses
                resolved_tool_calls = []
                for tc in msg.tool_calls:
                    tool_id = tc.get('id') if isinstance(tc, dict) else getattr(tc, 'id', None)
                    if tool_id and tool_id not in pending_tool_calls:
                        resolved_tool_calls.append(tc)

                # If all tool_calls are resolved, keep the message as-is
                if len(resolved_tool_calls) == len(msg.tool_calls):
                    final_validated.append(msg)
                # If some are resolved, update the message
                elif resolved_tool_calls:
                    cleaned_msg = AIMessage(content=msg.content, tool_calls=resolved_tool_calls)
                    final_validated.append(cleaned_msg)
                # If none are resolved, strip all tool_calls
                else:
                    cleaned_msg = AIMessage(content=msg.content)
                    final_validated.append(cleaned_msg)
            else:
                final_validated.append(msg)
        validated = final_validated

    return validated


def _clean_message_history(messages) -> List[Any]:
    """Clean message history with context length management"""
    validated_messages = _validate_and_fix_message_sequence(messages)
    cleaned = []

    # Keep system message (first message)
    if validated_messages and isinstance(validated_messages[0], SystemMessage):
        cleaned.append(validated_messages[0])

    # Keep last N messages to manage context length
    recent_messages = validated_messages[1:] if validated_messages else []

    if len(recent_messages) > MAX_MESSAGES_HISTORY:
        # Keep the most recent messages
        recent_messages = recent_messages[-MAX_MESSAGES_HISTORY:]

    # Process recent messages and summarize tool results
    for i, msg in enumerate(recent_messages):
        if isinstance(msg, (SystemMessage, HumanMessage, AIMessage)):
            cleaned.append(msg)
        elif hasattr(msg, 'tool_call_id'):
            #  Don't summarize recent tool results - agent needs to see them
            # Only summarize if we have too many messages
            if len(recent_messages) > MAX_MESSAGES_HISTORY:
                summarized_msg = _summarize_tool_result(msg)
                if summarized_msg:
                    cleaned.append(summarized_msg)
            else:
                # Keep full tool results for recent messages
                cleaned.append(msg)

    return cleaned


def _summarize_tool_result(tool_result_msg) -> Optional[object]:
    """Summarize tool results to reduce context length"""
    try:
        from langchain_core.messages import ToolMessage

        # Extract tool result content
        if hasattr(tool_result_msg, 'content'):
            content = tool_result_msg.content
        else:
            content = str(tool_result_msg)

        # If content is too long, summarize it
        if len(content) > MAX_TOOL_RESULT_LENGTH:
            # Try to extract key information
            if isinstance(content, str):
                # For JSON responses, try to extract key fields
                try:
                    import json
                    data = json.loads(content)
                    if isinstance(data, dict):
                        # Create summary with key fields
                        summary_fields = {}
                        for key in ['id', 'subject', 'snippet', 'from', 'to', 'date', 'status', 'result']:
                            if key in data:
                                summary_fields[key] = data[key]

                        # Add truncated content if still too long
                        summary_content = json.dumps(summary_fields, indent=2)
                        if len(summary_content) > MAX_TOOL_RESULT_LENGTH:
                            summary_content = summary_content[:MAX_TOOL_RESULT_LENGTH] + "... [TRUNCATED]"

                        content = summary_content
                    else:
                        content = content[:MAX_TOOL_RESULT_LENGTH] + "... [TRUNCATED]"
                except (json.JSONDecodeError, TypeError):
                    content = content[:MAX_TOOL_RESULT_LENGTH] + "... [TRUNCATED]"
            else:
                content = str(content)[:MAX_TOOL_RESULT_LENGTH] + "... [TRUNCATED]"

        # Create summarized tool message
        return ToolMessage(
            content=content,
            tool_call_id=tool_result_msg.tool_call_id
        )

    except Exception:
        # If summarization fails, return truncated original
        try:
            from langchain_core.messages import ToolMessage
            content = str(tool_result_msg.content)[:1000] + "... [TRUNCATED]"
            return ToolMessage(
                content=content,
                tool_call_id=tool_result_msg.tool_call_id
            )
        except Exception:
            return None


# ============================================================================
# ROUTING FUNCTIONS
# ============================================================================

def should_continue(state: ChatState) -> Literal["execute_tools", "final"]:
    """Route based on tool calls"""
    return "execute_tools" if state.get("pending_tool_calls", False) else "final"


def check_for_error(state: ChatState) -> Literal["error", "continue"]:
    """Check for errors"""
    return "error" if state.get("error") else "continue"
