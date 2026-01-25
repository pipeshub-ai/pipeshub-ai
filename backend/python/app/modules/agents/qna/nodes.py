import asyncio
import functools
import hashlib
import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter

from app.modules.agents.qna.chat_state import ChatState
from app.modules.agents.qna.config import (
    MARKDOWN_MIN_LENGTH,
    MAX_CONTEXT_CHARS,
    MAX_ITERATION_COUNT,
    MAX_MESSAGES_HISTORY,
    MAX_MESSAGES_HISTORY_SIMPLE,
    MAX_RETRIES_PER_TOOL,
    MAX_TOOL_RESULT_LENGTH,
    MAX_TOOLS_PER_ITERATION,
    RESULT_PREVIEW_LENGTH,
    AnalysisConfig,
    MessageConfig,
    PerformanceConfig,
)
from app.modules.agents.qna.optimization import (
    DataOptimizer,
    LLMOptimizer,
    PromptOptimizer,
)
from app.modules.agents.qna.performance_tracker import get_performance_tracker
from app.modules.agents.qna.stream_utils import safe_stream_write
from app.modules.qna.agent_prompt import (
    create_agent_messages,
    detect_response_mode,
)
from app.utils.citations import (
    normalize_citations_and_chunks_for_agent,
    process_citations,
)
from app.utils.streaming import extract_json_from_string, stream_llm_response

# Backwards compatibility aliases for constants used in this file
HEADER_LENGTH_THRESHOLD = AnalysisConfig.HEADER_LENGTH_THRESHOLD
STREAMING_CHUNK_DELAY = PerformanceConfig.STREAMING_CHUNK_DELAY
STREAMING_FALLBACK_DELAY = PerformanceConfig.STREAMING_FALLBACK_DELAY
TUPLE_RESULT_LEN = AnalysisConfig.TUPLE_RESULT_LEN
SHORT_ERROR_TEXT_THRESHOLD = AnalysisConfig.SHORT_ERROR_TEXT_THRESHOLD
RECENT_CALLS_WINDOW = AnalysisConfig.RECENT_CALLS_WINDOW
REPETITION_MIN_COUNT = AnalysisConfig.REPETITION_MIN_COUNT
JSON_RICH_OBJECT_MIN_KEYS = AnalysisConfig.JSON_RICH_OBJECT_MIN_KEYS
KEY_VALUE_PATTERN_MIN_COUNT = AnalysisConfig.KEY_VALUE_PATTERN_MIN_COUNT
RESULT_PREVIEW_MAX_LEN = MessageConfig.RESULT_PREVIEW_MAX
RESULT_STR_LONG_THRESHOLD = MessageConfig.RESULT_STR_LONG_THRESHOLD
ID_VALUE_MIN_LENGTH = AnalysisConfig.ID_VALUE_MIN_LENGTH
REPEATED_SUCCESS_MIN_COUNT = AnalysisConfig.REPEATED_SUCCESS_MIN_COUNT
COMPREHENSIVE_SUCCESS_MIN = AnalysisConfig.COMPREHENSIVE_SUCCESS_MIN
COMPREHENSIVE_TYPES_MIN = AnalysisConfig.COMPREHENSIVE_TYPES_MIN
PARTIAL_SUCCESS_MIN = AnalysisConfig.PARTIAL_SUCCESS_MIN
PARTIAL_DATA_MIN = AnalysisConfig.PARTIAL_DATA_MIN
RECENT_FAILURE_WINDOW = AnalysisConfig.RECENT_FAILURE_WINDOW
PING_REPEAT_MIN = AnalysisConfig.PING_REPEAT_MIN
SUSPICIOUS_RESPONSE_MIN = AnalysisConfig.SUSPICIOUS_RESPONSE_MIN
LOOP_DETECTION_MIN_CALLS = PerformanceConfig.LOOP_DETECTION_MIN_CALLS
LOOP_DETECTION_MAX_UNIQUE_TOOLS = PerformanceConfig.LOOP_DETECTION_MAX_UNIQUE_TOOLS

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

            # JIRA-specific errors (most common)
            if "unbounded jql" in error_lower or "unbounded query" in error_lower:
                retry_guidance.append("  🚨 **UNBOUNDED JQL ERROR** - You MUST add a TIME/DATE FILTER!")
                retry_guidance.append("  ⚠️ **ROOT CAUSE**: JIRA won't scan all tickets without time limits")
                retry_guidance.append("  ✅ **CORRECT FIX**: Add `AND updated >= -30d` (last 30 days) to your JQL")
                retry_guidance.append("  ✅ **ALTERNATIVE**: Add `AND created >= -90d` (last 90 days)")
                retry_guidance.append("  ❌ **DON'T**: Just add status filters - that won't fix unbounded errors!")
                retry_guidance.append("  📝 **EXAMPLE**: If your JQL is `project IN (ESP, PA) AND assignee = currentUser() AND resolution IS EMPTY`")
                retry_guidance.append("                 → FIX IT TO: `project IN (ESP, PA) AND assignee = currentUser() AND resolution IS EMPTY AND updated >= -30d`")
                retry_guidance.append("  🔄 **ACTION**: RETRY with time filter added: `AND updated >= -30d` or `AND created >= -90d`")
            elif "username or property" in error_lower and "must be provided" in error_lower:
                retry_guidance.append("  🚨 **CRITICAL FIX**: jira_search_users requires 'username' parameter, not 'query'")
                retry_guidance.append("  ✅ **CORRECT**: Use the user's display name or email as 'username' parameter")
                retry_guidance.append("  ❌ **WRONG**: Don't search for users to fix JQL errors - just fix the JQL!")
                retry_guidance.append("  💡 **HINT**: Use `currentUser()` in JQL instead of searching for user IDs")
            elif "uuid" in error_lower or "valid uuid" in error_lower or "validation" in error_lower:
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
                context_parts.append("   🔄 **CAN RETRY ONCE** - Analyze the error and fix the parameters")
                # Add specific guidance based on tool and error
                if tool_name == "jira_search_issues" and "unbounded" in error_preview.lower():
                    context_parts.append("   🎯 **SPECIFIC FIX**: Add a TIME FILTER → `AND updated >= -30d` (last 30 days)")
                    context_parts.append("   ❌ **DON'T**: Just add status filters - that won't fix unbounded!")
                    context_parts.append("   ❌ **DON'T**: Call jira_search_users or other irrelevant tools")
                    context_parts.append("   ✅ **DO**: Retry jira_search_issues with TIME FILTER added to your JQL")
                elif tool_name == "jira_search_users" and "username" in error_preview.lower():
                    context_parts.append("   🎯 **SPECIFIC FIX**: If you're trying to fix a JQL error, DON'T search users")
                    context_parts.append("   ❌ **DON'T**: Keep trying to search users when the issue is JQL syntax")
                    context_parts.append("   ✅ **DO**: Use `currentUser()` in your JQL instead of user IDs")
                else:
                    context_parts.append("   💡 **ACTION**: Correct the parameters based on error message and retry")

        # Check if we're seeing repeated similar errors (agent not learning)
        total_failures = sum(details["count"] for details in failed_tool_details.values())
        unique_tools_failed = len(failed_tool_details)

        if total_failures >= PerformanceConfig.MIN_FAILURES_FOR_STOP_RETRY and unique_tools_failed <= PerformanceConfig.MAX_UNIQUE_TOOLS_FAILED:
            # Same tool(s) failing multiple times = agent not learning from errors
            context_parts.append("\n🚨 **STOP RETRYING** - You've failed 3+ times with similar errors")
            context_parts.append("📝 **ACTION**: The issue is likely with the API requirements or user permissions")
            context_parts.append("💡 **WHAT TO DO**: Inform the user about the error and ask them for help")
            context_parts.append("❌ **DON'T**: Keep trying variations of the same broken approach")
            context_parts.append("✅ **DO**: Explain what failed and ask user to clarify their requirements")
        elif any(retry_count.get(tool, 0) < MAX_RETRIES_PER_TOOL for tool in failed_tool_details):
            context_parts.append("\n✅ **YOU CAN RETRY ONCE** - Fix the parameters and try again")
            context_parts.append("🔍 **HOW TO FIX**: Read error messages carefully, they tell you exactly what's wrong")
            context_parts.append("⚠️ **WARNING**: If retry fails again, STOP and ask the user for help")
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

    from app.modules.agents.qna.tool_system import get_recently_failed_tools
    blocked_tools = get_recently_failed_tools(state, None)

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

async def analyze_query_node(state: ChatState, config: RunnableConfig, writer: StreamWriter) -> ChatState:
    """Analyze query complexity, follow-ups, and determine retrieval needs"""
    try:
        logger = state["logger"]

        # ⚡ PERFORMANCE: Track timing
        perf = get_performance_tracker(state)
        perf.start_step("analyze_query_node")

        safe_stream_write(writer, {"event": "status", "data": {"status": "analyzing", "message": "🧠 Analyzing your request..."}}, config)

        query = state["query"].lower()
        previous_conversations = state.get("previous_conversations", [])

        # ⚡ TRILLION-DOLLAR FIX: Use intelligent conversation memory for follow-up detection
        from app.modules.agents.qna.conversation_memory import ConversationMemory

        # Check if this is a contextual follow-up that can reuse previous data
        is_follow_up_from_memory = ConversationMemory.should_reuse_tool_results(
            state["query"],  # Use original query, not lowercased
            previous_conversations
        )
        logger.info(f"🧠 ConversationMemory follow-up detection: {is_follow_up_from_memory}")
        logger.info(f"🧠 Query: '{state['query']}'")
        logger.info(f"🧠 Previous conversations: {len(previous_conversations)} turns")

        is_follow_up = is_follow_up_from_memory

        # Also check for traditional follow-up patterns (for non-action follow-ups)
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
            is_follow_up or  # Contextual follow-up from memory system
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

        # Note: needs_internal_data is kept for backward compatibility and other logic
        # but it no longer controls retrieval - the LLM will decide when to call the retrieval tool
        needs_internal_data = (
            has_kb_filter or
            has_app_filter or
            any(keyword in query for keyword in internal_keywords)
        )
        if is_follow_up:
            logger.info("Follow-up detected - LLM will decide if retrieval tool is needed")

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

        # ⚡ PERFORMANCE: Finish step timing
        duration = perf.finish_step(is_complex=is_complex, needs_data=needs_internal_data)
        logger.debug(f"⚡ analyze_query_node completed in {duration:.0f}ms")

        return state

    except Exception as e:
        logger.error(f"Error in query analysis: {str(e)}", exc_info=True)
        perf.finish_step(error=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state


# ============================================================================
# PHASE 2: SMART RETRIEVAL
# ============================================================================

async def conditional_retrieve_node(state: ChatState, config: RunnableConfig, writer: StreamWriter) -> ChatState:
    """Smart retrieval based on query analysis"""
    try:
        logger = state["logger"]

        if state.get("error"):
            return state

        # This node is deprecated - retrieval is now a tool
        # Keeping this for backward compatibility but it should not be called
        logger.warning("⚠️ conditional_retrieve_node called but retrieval is now a tool - this should not happen")
        state["search_results"] = []
        state["final_results"] = []
        return state

        logger.info("📚 Gathering knowledge sources...")
        safe_stream_write(writer, {"event": "status", "data": {"status": "retrieving", "message": "📚 Gathering knowledge sources..."}}, config)

        retrieval_service = state["retrieval_service"]
        arango_service = state["arango_service"]

        # Adjust limit based on complexity
        is_complex = state.get("query_analysis", {}).get("is_complex", False)
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
            is_agent=True,
        )

        # Handle case where retrieval service returns None (shouldn't happen, but safety check)
        if results is None:
            logger.warning("Retrieval service returned None, treating as empty results")
            state["search_results"] = []
            state["final_results"] = []
            return state

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

def prepare_agent_prompt_node(state: ChatState, config: RunnableConfig, writer: StreamWriter) -> ChatState:
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
            safe_stream_write(writer, {"event": "status", "data": {"status": "thinking", "message": "Planning complex workflow..."}}, config)

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

        # Get tools (using simplified tool system)
        from app.modules.agents.qna.tool_system import get_agent_tools
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

async def agent_node(state: ChatState, config: RunnableConfig, writer: StreamWriter) -> ChatState:
    """Agent with reasoning and dual-mode output capabilities"""
    try:
        logger = state["logger"]
        llm = state["llm"]

        # ⚡ PERFORMANCE: Track timing
        perf = get_performance_tracker(state)
        perf.start_step("agent_node")

        if state.get("error"):
            perf.finish_step(error=True)
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
            safe_stream_write(writer, {"event": "status", "data": {"status": "planning", "message": "Creating execution plan..."}}, config)
        elif iteration_count > 0:
            # Enhanced status with progress tracking
            recent_tools = [result.get("tool_name", "unknown") for result in state.get("all_tool_results", [])[-3:]]
            unique_recent = set(recent_tools)

            if len(unique_recent) == 1 and len(recent_tools) >= PING_REPEAT_MIN:
                safe_stream_write(writer, {"event": "status", "data": {"status": "adapting", "message": f"⚠️ Avoiding repetition - adapting plan (step {iteration_count + 1})..."}}, config)
            else:
                safe_stream_write(writer, {"event": "status", "data": {"status": "adapting", "message": f"Adapting plan (step {iteration_count + 1})..."}}, config)
        else:
            safe_stream_write(writer, {"event": "status", "data": {"status": "thinking", "message": "Processing your request..."}}, config)

        # Get tools (using simplified tool system)
        from app.modules.agents.qna.tool_system import get_agent_tools
        tools = get_agent_tools(state)

        # ⚡ NUCLEAR OPTIMIZATION: Cache bound LLM to eliminate tool binding overhead (1-2s saved!)
        cached_llm = state.get("_cached_llm_with_tools")
        cache_valid = cached_llm is not None and len(tools) == len(state.get("_cached_agent_tools", []))

        if cache_valid:
            logger.debug(f"⚡ Using cached LLM with {len(tools)} bound tools - skipping tool binding overhead")
            llm_with_tools = cached_llm
        elif tools:
            logger.debug(f"🔧 Binding {len(tools)} tools to LLM (first time or cache miss)")
            try:
                llm_with_tools = llm.bind_tools(tools)
                # Cache the bound LLM for future iterations
                state["_cached_llm_with_tools"] = llm_with_tools

            except (NotImplementedError, AttributeError) as e:
                logger.warning(f"LLM does not support tool binding: {e}")
                llm_with_tools = llm
                tools = []
        else:
            llm_with_tools = llm


        if state.get("all_tool_results"):
            # After tool execution: Use SMART SUMMARIES instead of full raw data
            # This gives LLM enough context to generate complete responses
            # while keeping token count low for speed

            # CRITICAL FIX: Inject full content IMMEDIATELY after first retrieval
            # This prevents agent from calling retrieval multiple times
            has_retrieval_data = state.get("final_results") and len(state["final_results"]) > 0
            should_inject_full_content = iteration_count > 0 and has_retrieval_data

            if should_inject_full_content:
                # Response generation stage: Check if we have retrieval data to inject
                if state.get("final_results") and len(state["final_results"]) > 0:
                    # CRITICAL: For final response, inject FULL content from retrieval results
                    # This allows LLM to generate accurate answers with proper context
                    logger.info(f"📄 Injecting {len(state['final_results'])} full retrieval results")

                    tool_context = "\n\n" + "=" * 80 + "\n"
                    tool_context += "📚 RETRIEVED DATA - HIGH QUALITY INFORMATION\n"
                    tool_context += "=" * 80 + "\n\n"
                    tool_context += "⚠️ **YOU MUST USE THIS DATA**: This is authoritative information retrieved from internal knowledge.\n\n"
                    tool_context += "**DECISION RULES**:\n"
                    tool_context += "1. IF this data answers the question → Use it and provide answer with citations [R1-1]\n"
                    tool_context += "2. IF this data is insufficient → Call retrieval again with a DIFFERENT, more specific query\n"
                    tool_context += "3. DO NOT hallucinate. DO NOT make up facts. ONLY use data below or call retrieval for more.\n"
                    tool_context += "4. Use conversation history to extract parameters (project keys, user names, etc.) - DON'T hardcode!\n\n"
                    tool_context += f"**CURRENT DATA**: {len(state['final_results'])} blocks available below\n\n"

                    for idx, result in enumerate(state["final_results"][:15], 1):  # Max 15 blocks (~22k chars = ~5.5k tokens)
                        block_num = result.get("block_number", f"Block-{idx}")
                        content = result.get("content", "")
                        metadata = result.get("metadata", {})

                        if isinstance(content, list):
                            # Handle list format (e.g., [{"type": "text", "text": "..."}])
                            text_parts = []
                            for item in content:
                                if isinstance(item, dict) and item.get("type") == "text":
                                    text_parts.append(item.get("text", ""))
                            content = "\n".join(text_parts)
                        elif isinstance(content, dict):
                            content = content.get("text", str(content))

                        # Truncate very long content blocks (keep first N chars each for speed)
                        if len(content) > PerformanceConfig.MAX_CONTENT_BLOCK_LENGTH:
                            content = content[:PerformanceConfig.MAX_CONTENT_BLOCK_LENGTH] + "..."

                        tool_context += f"\n### {block_num}\n"
                        tool_context += f"**Source**: {metadata.get('record_name', 'Unknown')}\n"
                        tool_context += f"**Content**: {content}\n"
                        tool_context += f"**→ Cite as: [{block_num}]**\n"
                        tool_context += "-" * 80 + "\n"

                    tool_context += "\n" + "=" * 80 + "\n"
                    tool_context += "✅ **INSTRUCTIONS**: Answer the user's question using ONLY the data above.\n"
                    tool_context += "Include citations [block_number] after each fact. DO NOT call retrieval again.\n"
                    tool_context += "=" * 80 + "\n"
                else:
                    # No retrieval data: Use smart summary
                    tool_context = "\n\n**Available Data:**\n"
                    for result in state["all_tool_results"][-3:]:  # Last 3 tool results
                        tool_name = result.get("tool_name", "unknown")
                        tool_result = result.get("result", {})

                        # Create smart summary
                        summary = DataOptimizer.create_summary(tool_result, tool_name)
                        tool_context += f"\n{summary}"

                    tool_context += "\n\n**Note**: Use this data to provide a complete, accurate response. All data is available."
            else:
                # Tool selection stage: Minimal context
                tool_context = PromptOptimizer.create_concise_tool_context(state["all_tool_results"])

            # Add output format reminder (concise)
            if has_internal_knowledge:
                tool_context += "\n\n**Output**: Structured JSON with citations"
            else:
                tool_context += "\n\n**Output**: Markdown format"

            if state["messages"] and isinstance(state["messages"][-1], HumanMessage):
                state["messages"][-1].content += tool_context

        # ⚡ OPTIMIZATION: Smart message history management (keep enough context)
        is_complex = state.get("requires_planning", False)
        max_messages = 15 if is_complex else 10  # Enough context for complete answers

        cleaned_messages = PromptOptimizer.optimize_message_history(
            state["messages"],
            max_messages=max_messages,
            max_context_chars=MAX_CONTEXT_CHARS
        )

        # Log optimization impact
        original_count = len(state["messages"])
        optimized_count = len(cleaned_messages)
        if original_count > optimized_count:
            logger.debug(f"⚡ Optimized messages: {original_count} → {optimized_count} ({original_count - optimized_count} removed)")

        # ⚡ CRITICAL: Validate message sequence before sending to LLM (required by most providers)
        is_valid, error_msg = PromptOptimizer.validate_message_sequence(cleaned_messages)
        if not is_valid:
            logger.error(f"❌ Message validation failed: {error_msg}")
            logger.warning("⚠️ Message sequence invalid - cleaning up tool calls without responses")

            # Fix invalid messages by removing tool_calls from AIMessages without responses
            from langchain_core.messages import AIMessage, ToolMessage
            fixed_messages = []
            pending_tool_calls = set()

            for msg in cleaned_messages:
                if isinstance(msg, AIMessage):
                    if hasattr(msg, 'tool_calls') and msg.tool_calls:
                        # Track tool_call_ids
                        tool_call_ids = set()
                        for tc in msg.tool_calls:
                            tool_id = tc.get('id') if isinstance(tc, dict) else getattr(tc, 'id', None)
                            if tool_id:
                                tool_call_ids.add(tool_id)

                        # Check if all ToolMessages exist in remaining messages
                        remaining_messages = cleaned_messages[cleaned_messages.index(msg) + 1:]
                        all_responses_exist = all(
                            any(isinstance(m, ToolMessage) and getattr(m, 'tool_call_id', None) == tid
                                for m in remaining_messages)
                            for tid in tool_call_ids
                        )

                        if all_responses_exist:
                            fixed_messages.append(msg)
                            pending_tool_calls.update(tool_call_ids)
                        else:
                            # Remove tool_calls - create new AIMessage without them
                            fixed_messages.append(AIMessage(content=msg.content or ""))
                    else:
                        fixed_messages.append(msg)
                elif isinstance(msg, ToolMessage):
                    tool_call_id = getattr(msg, 'tool_call_id', None)
                    if tool_call_id in pending_tool_calls:
                        fixed_messages.append(msg)
                        pending_tool_calls.discard(tool_call_id)
                    # Skip orphaned ToolMessages (no corresponding AIMessage)
                else:
                    fixed_messages.append(msg)

            cleaned_messages = fixed_messages

            # Re-validate after fix
            is_valid, error_msg = PromptOptimizer.validate_message_sequence(cleaned_messages)
            if not is_valid:
                logger.error(f"❌ Message validation still failed after fix: {error_msg}")
                logger.warning("⚠️ Using minimal message set to prevent API error")
                # Last resort: keep only system messages and the most recent human message
                system_msgs = [m for m in cleaned_messages if isinstance(m, SystemMessage)]
                human_msgs = [m for m in cleaned_messages if isinstance(m, HumanMessage)]
                cleaned_messages = system_msgs + (human_msgs[-1:] if human_msgs else [])

        # Estimate token count for monitoring
        estimated_tokens = LLMOptimizer.estimate_token_count(cleaned_messages)
        if estimated_tokens > 0:
            logger.debug(f"📊 Estimated tokens: ~{estimated_tokens}")

        # Simple debug logging
        if state.get("all_tool_results"):
            logger.debug(f"🔍 Agent context includes {len(state['all_tool_results'])} tool results")

        # ⚡ OPTIMIZATION: Use optimized LLM invocation
        logger.debug(f"⚡ Invoking LLM (iteration {iteration_count}) with optimized prompt")

        llm_start = time.time()

        # Initialize LLM optimizer if not exists
        if not hasattr(state, '_llm_optimizer'):
            state['_llm_optimizer'] = LLMOptimizer()

        llm_optimizer = state.get('_llm_optimizer', LLMOptimizer())

        # Use optimized invoke with timeout protection
        try:
            response = await llm_optimizer.optimized_invoke(
                llm_with_tools,
                cleaned_messages,
                timeout=25.0  # 25s timeout for safety
            )
        except TimeoutError as te:
            logger.error(f"⚠️ LLM call timeout: {te}")
            # Fallback to direct invoke
            response = await llm_with_tools.ainvoke(cleaned_messages)

        llm_duration = (time.time() - llm_start) * 1000

        # Track LLM performance
        perf.track_llm_call(llm_duration)
        logger.debug(f"⚡ LLM call completed in {llm_duration:.0f}ms")

        # Add response to messages
        state["messages"].append(response)

        # Check for tool calls
        if hasattr(response, 'tool_calls') and response.tool_calls:
            # Smart duplicate detection: Only filter obvious duplicates with identical params
            # Allow intentional retries with different parameters or for refinement
            filtered_tool_calls = []
            has_retrieval_data = state.get("final_results") and len(state["final_results"]) > 0
            executed_tool_signatures = set()

            # Build signatures of previously executed tools
            for tool_result in state.get("all_tool_results", []):
                if isinstance(tool_result, dict):
                    tool_sig = f"{tool_result.get('name')}:{str(tool_result.get('args', {}))}"
                    executed_tool_signatures.add(tool_sig)

            for tc in response.tool_calls:
                tool_name = tc.get("name") if isinstance(tc, dict) else tc.name
                tool_args = tc.get("args", {}) if isinstance(tc, dict) else (tc.args if hasattr(tc, 'args') else {})
                tool_signature = f"{tool_name}:{str(tool_args)}"

                # Check for exact duplicate (same tool + same args)
                if tool_signature in executed_tool_signatures:
                    logger.info(f"⚠️ Skipping exact duplicate call: {tool_name} with same params")
                    continue

                # For retrieval: allow different queries but block true duplicates
                if tool_name == "retrieval_search_internal_knowledge":
                    # Smart blocking: Allow refinement, block wasteful duplicates
                    # This enables standard agent behavior (like n8n, flowise, etc)

                    # Get the query from this call
                    current_query = tool_args.get("query", "")

                    # Track previous retrieval queries in state
                    if "retrieval_queries" not in state:
                        state["retrieval_queries"] = []

                    # Check for EXACT duplicate query
                    if current_query and current_query in state["retrieval_queries"]:
                        logger.warning(f"⚠️ Blocking EXACT DUPLICATE retrieval query: '{current_query[:80]}...'")
                        logger.info(f"🎯 Agent should use existing {len(state.get('final_results', []))} results instead")
                        continue

                    # Check for SIMILAR queries using simple word overlap (> 60% overlap = duplicate)
                    # This catches cases like "Asana Q4 2024" vs "Asana fourth quarter fiscal 2024"
                    should_skip_similar = False
                    if current_query and state["retrieval_queries"]:
                        current_words = set(current_query.lower().split())
                        for prev_query in state["retrieval_queries"]:
                            prev_words = set(prev_query.lower().split())
                            if len(current_words) > 0 and len(prev_words) > 0:
                                overlap = len(current_words & prev_words)
                                similarity = overlap / max(len(current_words), len(prev_words))
                                if similarity > PerformanceConfig.QUERY_SIMILARITY_THRESHOLD:  # More than 60% overlap = similar enough
                                    logger.warning(f"⚠️ Blocking SIMILAR retrieval query ({similarity:.0%} overlap)")
                                    logger.warning(f"   Previous: '{prev_query[:60]}...'")
                                    logger.warning(f"   Current:  '{current_query[:60]}...'")
                                    logger.info(f"🎯 Agent should use existing {len(state.get('final_results', []))} results")
                                    should_skip_similar = True
                                    break  # Found similar query, no need to check more

                    if should_skip_similar:
                        continue  # Skip this tool call - don't add to filtered list

                    # Check for excessive calls (safety limit: max N retrieval calls for same topic)
                    # Chatbot does ONE retrieval, agent should match that efficiency
                    retrieval_count = len(state.get("retrieval_queries", []))
                    if retrieval_count >= PerformanceConfig.MAX_RETRIEVAL_CALLS:
                        logger.warning(f"⚠️ Blocking retrieval - already made {retrieval_count} calls")
                        logger.warning(f"   Agent has {len(state.get('final_results', []))} results - should answer now")
                        logger.info("🎯 Chatbot does 1 retrieval → answer. Agent should match that efficiency.")
                        continue

                    # This is a NEW query or first call - allow it
                    if current_query:
                        state["retrieval_queries"].append(current_query)
                        logger.info(f"✅ Allowing retrieval call #{retrieval_count + 1}: '{current_query[:80]}...'")

                        # Warn if calling again (should answer after first retrieval like chatbot)
                        existing_results = len(state.get("final_results", []))
                        if existing_results >= PerformanceConfig.MIN_RESULTS_BEFORE_ANSWER:
                            logger.warning(f"⚠️ Agent already has {existing_results} results - should ANSWER, not retrieve again!")
                            logger.warning("💡 Chatbot answers immediately after retrieval. Agent should too.")

                filtered_tool_calls.append(tc)

            # Replace tool_calls with filtered version
            if filtered_tool_calls:
                response.tool_calls = filtered_tool_calls
                tool_count = len(filtered_tool_calls)
                logger.info(f"🔧 Agent decided to use {tool_count} tools")

                # Log which tools
                tool_names = []
                for tc in filtered_tool_calls:
                    tool_name = tc.get("name") if isinstance(tc, dict) else tc.name
                    tool_names.append(tool_name)
                logger.debug(f"Tools to execute: {', '.join(tool_names)}")

                state["pending_tool_calls"] = True
            else:
                # All tool calls were filtered out - force final response
                logger.info("✅ All planned tools already executed - generating final response")
                response.tool_calls = []
                state["pending_tool_calls"] = False

                # Extract response content
                if hasattr(response, 'content'):
                    response_content = response.content
                else:
                    response_content = str(response)

                # If response has content, store it
                if response_content and response_content.strip():
                    mode, parsed_content = detect_response_mode(response_content)
                    logger.info(f"📄 Response mode detected: {mode}")
                    state["response"] = parsed_content
                    state["response_mode"] = mode
                else:
                    # Empty response after filtering is OK - final_response_node will generate it
                    # Keep the AIMessage in history as it shows the agent's reasoning
                    logger.info("⚠️ Empty response after filtering - final_response_node will generate answer")
                    state["response"] = None
                    state["response_mode"] = None
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

        # ⚡ PERFORMANCE: Finish step timing
        duration = perf.finish_step(
            iteration=iteration_count,
            tool_calls=len(response.tool_calls) if hasattr(response, 'tool_calls') and response.tool_calls else 0
        )
        logger.debug(f"⚡ agent_node completed in {duration:.0f}ms")

        return state

    except Exception as e:
        logger.error(f"Error in agent: {str(e)}", exc_info=True)
        perf.finish_step(error=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state


# ============================================================================
# PHASE 6: TOOL EXECUTION
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
                    error_value = data["error"]
                    # If error field exists and is not None, it's a failure
                    if error_value is not None:
                        return False

                # Check for status field (from tool_system errors and API responses)
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

    except Exception:
        pass

    # Fallback: if it starts with "Error:" it's a failure
    if result_str.startswith("Error:") or result_str.startswith("Error executing"):
        return False

    # Default: assume success
    return True


async def tool_execution_node(state: ChatState, config: RunnableConfig, writer: StreamWriter) -> ChatState:
    """Execute tools with planning context"""
    try:
        logger = state["logger"]

        # ⚡ PERFORMANCE: Track timing
        perf = get_performance_tracker(state)
        perf.start_step("tool_execution_node")

        iteration = len(state.get("all_tool_results", []))
        safe_stream_write(writer, {"event": "status", "data": {"status": "executing", "message": f" Executing tools (step {iteration + 1})..."}}, config)

        if state.get("error"):
            perf.finish_step(error=True)
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
        original_tool_call_count = len(tool_calls)

        # CRITICAL: Deduplicate tool calls to prevent duplicate operations
        # This is especially important for create/update/delete operations
        seen_calls = {}  # (tool_name, args_hash) -> tool_call
        deduplicated_calls = []
        duplicate_count = 0

        for tool_call in tool_calls:
            # Extract tool name and args
            if isinstance(tool_call, dict):
                tool_name = tool_call.get("name") or tool_call.get("function", {}).get("name", "")
                tool_args = tool_call.get("args", {})
                if not tool_args and "function" in tool_call:
                    function_data = tool_call["function"]
                    tool_args = function_data.get("arguments", {})
                    if isinstance(tool_args, str):
                        try:
                            tool_args = json.loads(tool_args)
                        except json.JSONDecodeError:
                            tool_args = {}
            else:
                tool_name = tool_call.name
                tool_args = tool_call.args if hasattr(tool_call, 'args') else {}

            # Create a hash of the arguments for comparison
            # Sort dict keys to ensure consistent hashing
            args_str = json.dumps(tool_args, sort_keys=True) if tool_args else ""
            args_hash = hashlib.md5(args_str.encode()).hexdigest()
            call_key = (tool_name, args_hash)

            # Check if this is a critical operation (create, update, delete)
            is_critical = any(op in tool_name.lower() for op in ['create', 'update', 'delete', 'remove', 'add'])

            if call_key in seen_calls:
                duplicate_count += 1
                if is_critical:
                    logger.warning(f"🚫 BLOCKED duplicate critical operation: {tool_name} with identical args. This would create duplicate data. Keeping only the first call.")
                else:
                    logger.warning(f"⚠️ Detected duplicate tool call: {tool_name} with identical args. Keeping only the first call.")
                continue

            seen_calls[call_key] = tool_call
            deduplicated_calls.append(tool_call)

        if duplicate_count > 0:
            logger.warning(f"🛡️ Deduplication: Removed {duplicate_count} duplicate tool call(s). Original: {original_tool_call_count}, After dedup: {len(deduplicated_calls)}")

        tool_calls = deduplicated_calls

        # Limit tool calls per iteration
        if len(tool_calls) > MAX_TOOLS_PER_ITERATION:
            logger.warning(f"⚠️ Too many tool calls ({len(tool_calls)}) - limiting to {MAX_TOOLS_PER_ITERATION}")
            tool_calls = tool_calls[:MAX_TOOLS_PER_ITERATION]

        # Get available tools
        from app.modules.agents.qna.tool_system import get_agent_tools
        tools = get_agent_tools(state)
        tools_by_name = {tool.name: tool for tool in tools}

        tool_messages = []
        tool_results = []

        # ⚡ PERFORMANCE: Parallel async execution for maximum speed
        logger.info(f"🚀 Preparing to execute {len(tool_calls)} tool(s) IN PARALLEL...")

        # Create execution tasks for parallel execution
        async def execute_single_tool(tool_call: Any) -> dict[str, Any]:  # noqa: ANN401
            """
            Execute a single tool with async support and timing.
            Uses standard asyncio patterns to preserve StreamWriter context.
            """
            # ⚡ Track individual tool execution time
            tool_start_time = time.time()

            tool_name = tool_call.get("name") if isinstance(tool_call, dict) else tool_call.name

            # Handle both tool_call.args and tool_call.function formats
            # Different LLM providers use different formats - support both
            if isinstance(tool_call, dict):
                tool_args = tool_call.get("args", {})
                # Check for function format (used by OpenAI, Gemini, and others)
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

            # Handle nested kwargs format (used by some LLM providers)
            # Some providers wrap arguments in a 'kwargs' key - unwrap it for consistency
            # This ensures compatibility across OpenAI, Anthropic, Gemini, and other providers
            if isinstance(tool_args, dict) and "kwargs" in tool_args:
                # If kwargs is the only key, unwrap it
                if len(tool_args) == 1:
                    tool_args = tool_args["kwargs"]
                    logger.debug(f"  Unwrapped kwargs: {tool_args}")
                # If kwargs exists but there are other keys, check if kwargs contains the actual args
                elif isinstance(tool_args.get("kwargs"), dict) and len(tool_args.get("kwargs", {})) > 0:
                    # Prefer kwargs if it has content, otherwise keep original
                    tool_args = tool_args["kwargs"]
                    logger.debug(f"  Unwrapped kwargs (had other keys): {tool_args}")

            try:
                result = None

                if tool_name in tools_by_name:
                    tool = tools_by_name[tool_name]
                    logger.info(f"▶️ Executing: {tool_name}")
                    logger.debug(f"  Args: {tool_args}")

                    # STANDARD EXECUTION PATTERN - Preserves StreamWriter context
                    # Check if tool has native async implementation
                    if hasattr(tool, 'arun'):
                        # Native Async: Await directly
                        # This preserves context automatically
                        result = await tool.arun(tool_args)
                    elif hasattr(tool, '_run'):
                        # Native Sync: Offload to thread pool properly
                        # functools.partial passes arguments without wrappers
                        # run_in_executor keeps us connected to the main program flow
                        loop = asyncio.get_running_loop()
                        result = await loop.run_in_executor(
                            None,
                            functools.partial(tool._run, **tool_args)
                        )
                    else:
                        # Fallback to sync run
                        loop = asyncio.get_running_loop()
                        result = await loop.run_in_executor(
                            None,
                            functools.partial(tool.run, **tool_args)
                        )

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

                # CRITICAL: Handle structured retrieval tool output
                # Retrieval tool returns RetrievalToolOutput with minimal summary + full metadata
                llm_content = result  # Default: pass result as-is

                if tool_name == "retrieval_search_internal_knowledge":
                    logger.info("🎯 MATCHED RETRIEVAL TOOL - Extracting structured output")
                    logger.debug(f"   Raw result type: {type(result)}")

                    # Handle multiple formats (dict, str, or Pydantic model)
                    import ast
                    import json as json_module

                    from app.agents.actions.retrieval.retrieval import (
                        RetrievalToolOutput,
                    )

                    retrieval_output = None

                    # Try parsing as dict first
                    if isinstance(result, dict) and "content" in result and "final_results" in result:
                        try:
                            retrieval_output = RetrievalToolOutput(**result)
                            logger.debug("   ✅ Parsed as dict")
                        except Exception as e:
                            logger.warning(f"   ⚠️ Failed to parse dict: {e}")

                    # Try parsing as JSON string or Python repr string
                    elif isinstance(result, str):
                        # Try JSON first (double quotes)
                        try:
                            result_dict = json_module.loads(result)
                            if "content" in result_dict and "final_results" in result_dict:
                                retrieval_output = RetrievalToolOutput(**result_dict)
                                logger.debug("   ✅ Parsed as JSON string")
                        except json_module.JSONDecodeError:
                            # Try Python literal eval (handles single quotes from str(dict))
                            try:
                                result_dict = ast.literal_eval(result)
                                if isinstance(result_dict, dict) and "content" in result_dict and "final_results" in result_dict:
                                    retrieval_output = RetrievalToolOutput(**result_dict)
                                    logger.debug("   ✅ Parsed as Python repr string (ast.literal_eval)")
                            except Exception as e2:
                                logger.warning(f"   ⚠️ Failed to parse string as JSON or Python repr: {e2}")

                    # Already a Pydantic model
                    elif isinstance(result, RetrievalToolOutput):
                        retrieval_output = result
                        logger.debug("   ✅ Already RetrievalToolOutput")

                    if retrieval_output:
                        # Extract structured data
                        llm_content = retrieval_output.content
                        final_results = retrieval_output.final_results
                        virtual_record_id_to_result = retrieval_output.virtual_record_id_to_result

                        # Store in graph state for citation processing
                        state["final_results"] = final_results
                        state["virtual_record_id_to_result"] = virtual_record_id_to_result

                        logger.info(f"✅ EXTRACTED {len(final_results)} final_results from structured output")
                        logger.info(f"✅ EXTRACTED {len(virtual_record_id_to_result)} virtual_record_id_to_result from structured output")
                        logger.info(f"📉 Token optimization: LLM gets {len(llm_content)} char summary (not {len(json.dumps(final_results))} char full content)")
                    else:
                        logger.error(f"❌ Could not parse retrieval result (type: {type(result)})")
                        logger.error(f"   Result preview: {str(result)[:200]}")
                        # FALLBACK: Use minimal error message instead of full result
                        llm_content = "✅ Retrieval completed but failed to extract structured data. Results available in state for final response."
                        logger.warning(f"⚠️ Using fallback message for LLM ({len(llm_content)} chars)")

                tool_result = {
                    "tool_name": tool_name,
                    "result": llm_content,  # Use extracted content, not raw result
                    "status": status,
                    "tool_id": tool_id,
                    "args": tool_args,
                    "execution_timestamp": datetime.now().isoformat(),
                    "iteration": iteration
                }

                # ⚡ OPTIMIZATION: Format optimization WITHOUT data loss
                # For retrieval tool, llm_content is already optimized - don't compress further
                # For other tools, apply compression
                if tool_name == "retrieval_search_internal_knowledge":
                    optimized_result = llm_content  # Already optimized by tool
                else:
                    optimized_result = PromptOptimizer.compress_tool_result(
                        llm_content,
                        max_chars=None,  # No hard limit - preserve data
                        preserve_data=True  # Keep all user-facing data
                    )
                tool_message = ToolMessage(content=optimized_result, tool_call_id=tool_id)

                # ⚡ PERFORMANCE: Track tool execution time
                tool_duration_ms = (time.time() - tool_start_time) * 1000
                perf.track_tool_execution(tool_name, tool_duration_ms, is_success)

                # Log correct status
                if is_success:
                    logger.info(f"✅ {tool_name} executed successfully in {tool_duration_ms:.0f}ms")

                    # **GENERIC FEEDBACK**: Provide intelligent guidance based on tool result analysis
                    data_analysis = analyze_tool_data_content(tool_name, str(llm_content))
                    if data_analysis["has_data"]:
                        logger.info(f"📊 {tool_name} retrieved data: {', '.join(data_analysis['data_types'])}")
                        if data_analysis["next_actions"]:
                            logger.info(f"🎯 Suggested next actions: {', '.join(data_analysis['next_actions'])}")
                else:
                    logger.error(f"❌ {tool_name} failed with error in {tool_duration_ms:.0f}ms")
                    logger.error(f"Error details: {str(llm_content)[:500]}")

                return {
                    "tool_result": tool_result,
                    "tool_message": tool_message
                }

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

                # Errors are usually short, keep them complete for debugging
                tool_message = ToolMessage(content=error_result, tool_call_id=tool_id)

                return {
                    "tool_result": tool_result,
                    "tool_message": tool_message
                }

        # PHASE 3: Execute all tools in parallel using asyncio.gather
        # Since we removed the custom event loops, this is now safe
        import asyncio
        execution_start = asyncio.get_event_loop().time()

        logger.info(f"⚡ Executing {len(tool_calls)} tool(s) in parallel...")

        try:
            # Execute all tools in parallel
            raw_results = await asyncio.gather(
                *[execute_single_tool(tc) for tc in tool_calls],
                return_exceptions=True
            )

            execution_end = asyncio.get_event_loop().time()
            execution_time_ms = (execution_end - execution_start) * 1000

            logger.info(f"✅ Completed {len(tool_calls)} tool(s) in {execution_time_ms:.0f}ms (parallel execution)")

            # Process results and extract records (like chatbot does)
            tool_records = []
            for r in raw_results:
                if isinstance(r, Exception):
                    logger.error(f"Execution panic: {r}")
                    continue

                tool_result = r["tool_result"]
                tool_results.append(tool_result)
                tool_messages.append(r["tool_message"])

                # CRITICAL: Extract records from tool results for citation normalization
                # Tools like fetch_full_record return {"ok": true, "records": [...]}
                # We need to accumulate these records for proper citation handling
                result_data = tool_result.get("result")
                if isinstance(result_data, dict) and "records" in result_data:
                    records_from_tool = result_data.get("records", [])
                    if records_from_tool:
                        tool_records.extend(records_from_tool)
                        logger.debug(f"📦 Extracted {len(records_from_tool)} records from tool {tool_result.get('tool_name')}")

            # ⚠️ CRITICAL: After first successful retrieval, inject ANSWER NOW reminder
            # This matches chatbot behavior: retrieve once → answer immediately
            for r in raw_results:
                if isinstance(r, Exception):
                    continue
                tool_result = r.get("tool_result", {})
                if tool_result.get("tool_name") == "retrieval_search_internal_knowledge" and tool_result.get("status") == "success":
                    retrieval_count = len(state.get("retrieval_queries", []))
                    existing_results = len(state.get("final_results", []))

                    if retrieval_count >= 1 and existing_results > 0:
                        # Inject a STRONG reminder message to answer now
                        from langchain_core.messages import HumanMessage

                        reminder_msg = HumanMessage(content=f"""
🎯 **CRITICAL**: You retrieved {existing_results} blocks. ANSWER NOW using that data.

**DO NOT** call retrieval again! You have the data. Just synthesize it into your answer with [R1-1] citations.

Chatbot does: Retrieve once → Answer. You should too.
""")
                        state["messages"].append(reminder_msg)
                        logger.info(f"💬 Injected ANSWER NOW reminder (agent has {existing_results} results)")
                        break  # Only inject once

        except Exception as e:
            logger.error(f"Error in parallel tool execution: {e}")
            # Fallback: If parallel execution fails, we already have some results
            pass

        # Store tool records in state for citation normalization
        if "tool_records" not in state:
            state["tool_records"] = []
        state["tool_records"].extend(tool_records)
        logger.debug(f"📊 Total tool records in state: {len(state['tool_records'])}")

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

        # ⚡ PERFORMANCE: Finish step timing
        duration = perf.finish_step(
            tool_count=len(tool_results),
            successful=sum(1 for r in tool_results if r.get("status") == "success"),
            failed=sum(1 for r in tool_results if r.get("status") == "error")
        )
        logger.debug(f"⚡ tool_execution_node completed in {duration:.0f}ms")

        return state

    except Exception as e:
        logger.error(f"Error in tool execution: {str(e)}", exc_info=True)
        perf.finish_step(error=True)
        state["error"] = {"status_code": 400, "detail": str(e)}
        return state


# ============================================================================
# PHASE 7: ENHANCED FINAL RESPONSE WITH DUAL-MODE SUPPORT
# ============================================================================

# 7. Fixed Final Response Node - Correct Streaming Format
async def final_response_node(state: ChatState, config: RunnableConfig, writer: StreamWriter) -> ChatState:
    """Generate final response with correct streaming format"""
    try:
        logger = state["logger"]

        # ⚡ PERFORMANCE: Track timing
        perf = get_performance_tracker(state)
        perf.start_step("final_response_node")
        llm = state["llm"]

        safe_stream_write(writer, {"event": "status", "data": {"status": "finalizing", "message": "Generating final response..."}}, config)

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
            safe_stream_write(writer, {"event": "answer_chunk", "data": {"chunk": error_content}}, config)

            # Send complete event with error response
            safe_stream_write(writer, {"event": "complete", "data": error_response}, config)

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

            safe_stream_write(writer, {"event": "status", "data": {"status": "delivering", "message": "Delivering response..."}}, config)

            # Normalize response format (handles markdown code blocks)
            final_content = _normalize_response_format(existing_response)

            # Process citations if available - use normalize_citations_and_chunks_for_agent
            final_results = state.get("final_results", [])
            # Ensure final_results is a list (might be stored as string or other format)
            if not isinstance(final_results, list):
                if isinstance(final_results, str):
                    try:
                        final_results = json.loads(final_results)
                    except (json.JSONDecodeError, TypeError):
                        logger.warning(f"⚠️ final_results is not a valid list or JSON: {type(final_results)}")
                        final_results = []
                else:
                    logger.warning(f"⚠️ final_results is not a list: {type(final_results)}")
                    final_results = []

            virtual_record_id_to_result = state.get("virtual_record_id_to_result", {})
            logger.debug(f"📊 Citation processing: final_results={len(final_results)} items, virtual_record_id_to_result={len(virtual_record_id_to_result)} records")

            if final_results:
                answer_text = final_content.get("answer", "")
                logger.debug(f"📝 Processing citations for answer (length: {len(answer_text)} chars)")
                # Normalize citations using the agent-specific function
                # Get tool records from state (accumulated during tool execution)
                tool_records = state.get("tool_records", [])
                logger.debug(f"📦 Using {len(tool_records)} tool records for citation normalization")
                normalized_answer, citations = normalize_citations_and_chunks_for_agent(
                    answer_text,
                    final_results,
                    virtual_record_id_to_result,
                    records=tool_records
                )
                logger.debug(f"✅ Citation normalization complete: {len(citations)} citations created")
                logger.debug(f"📄 Answer text length: {len(normalized_answer)} chars")
                logger.debug("***************************************************************************************************************")
                final_content["answer"] = normalized_answer
                final_content["citations"] = citations
            else:
                logger.warning("⚠️ No final_results available for citation processing")
                normalized_answer = final_content.get("answer", "")
                citations = final_content.get("citations", [])

            # Stream answer in word-based chunks (like chatbot.py)
            answer_text = normalized_answer
            words = re.findall(r'\S+', answer_text)
            target_words_per_chunk = 1  # Stream word by word for smooth experience

            accumulated = ""
            for i in range(0, len(words), target_words_per_chunk):
                chunk_words = words[i:i + target_words_per_chunk]
                chunk_text = ' '.join(chunk_words)
                # Build accumulated string incrementally to avoid quadratic complexity
                if accumulated:
                    accumulated += ' ' + chunk_text
                else:
                    accumulated = chunk_text

                safe_stream_write(writer, {
                    "event": "answer_chunk",
                    "data": {
                        "chunk": chunk_text,
                        "accumulated": accumulated,
                        "citations": citations  # Include citations in each chunk
                    }
                }, config)
                await asyncio.sleep(STREAMING_CHUNK_DELAY)

            # Send complete structure only at the end (properly formatted)
            completion_data = {
                "answer": answer_text,
                "citations": citations,  # Use normalized citations
                "confidence": final_content.get("confidence", "High"),
                "reason": final_content.get("reason", "Response generated"),
                "answerMatchType": final_content.get("answerMatchType", "Derived From Tool Execution"),
                "chunkIndexes": final_content.get("chunkIndexes", []),
                "workflowSteps": final_content.get("workflowSteps", [])
            }

            safe_stream_write(writer, {"event": "complete", "data": completion_data}, config)

            state["response"] = answer_text  # Store just the answer text
            state["completion_data"] = completion_data

            logger.debug(f"Delivered existing response: {len(answer_text)} chars")

            # ⚡ PERFORMANCE: Finish step timing and log summary even for cached responses
            duration = perf.finish_step(response_length=len(answer_text), cached=True)
            logger.debug(f"⚡ final_response_node completed in {duration:.0f}ms (cached response)")

            # ⚡ PERFORMANCE SUMMARY: Log complete performance report
            perf.log_summary(logger)

            # Store performance summary in state for API response
            state["performance_summary"] = perf.get_summary()

            return state

        # Generate new response if needed
        logger.debug("No usable response found, generating new response with LLM")

        # Convert LangChain messages to dict format
        # Clean message sequence first to ensure proper threading
        is_complex = state.get("requires_planning", False)
        cleaned_messages = _clean_message_history(state.get("messages", []), is_complex=is_complex)

        validated_messages = []
        for i, msg in enumerate(cleaned_messages):
            if isinstance(msg, SystemMessage):
                validated_messages.append({"role": "system", "content": msg.content})
            elif isinstance(msg, HumanMessage):
                validated_messages.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                # For AIMessage, preserve tool_calls ONLY if they're present and valid
                # CRITICAL: Many LLM providers (Anthropic, Gemini, etc.) require tool_calls to have corresponding results
                # If a tool_call doesn't have a result (e.g., execution was terminated), strip it out
                msg_dict = {"role": "assistant", "content": msg.content or ""}  # Ensure content is never None

                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    # Verify each tool_call has a corresponding ToolMessage in the next messages
                    # This prevents "tool_use without tool_result" errors across all LLM providers
                    validated_tool_calls = []
                    for tc in msg.tool_calls:
                        tool_id = tc.get('id') if isinstance(tc, dict) else getattr(tc, 'id', None)
                        if not tool_id:
                            continue  # Skip invalid tool calls

                        # Check if there's a ToolMessage with this tool_call_id after this message
                        has_result = False
                        for future_msg in cleaned_messages[i+1:i+10]:  # Check next 10 messages
                            if isinstance(future_msg, ToolMessage) and hasattr(future_msg, 'tool_call_id'):
                                if future_msg.tool_call_id == tool_id:
                                    has_result = True
                                    break

                        # Only include tool_call if it has a result
                        if has_result:
                            validated_tool_calls.append(tc)

                    # Only add tool_calls if there are validated ones
                    # This works for all LLM providers (OpenAI, Anthropic, Gemini, Claude, etc.)
                    if validated_tool_calls:
                        msg_dict["tool_calls"] = validated_tool_calls

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
                synthesis_instruction += "\n"
                synthesis_instruction += "**YOU MUST RESPOND** (do NOT return empty response!):\n"
                synthesis_instruction += "1. **Acknowledge the issue**: \"I tried to retrieve your tickets but encountered an error.\"\n"
                synthesis_instruction += "2. **Explain what happened**: Show the actual error (e.g., \"JIRA returned: 'Unbounded JQL query'\")\n"
                synthesis_instruction += "3. **What you tried**: \"I attempted to search with: [show the JQL query]\"\n"
                synthesis_instruction += "4. **Why it failed**: Explain the root cause in user-friendly terms\n"
                synthesis_instruction += "5. **Ask for help or clarification**: What information do you need from the user?\n"
                synthesis_instruction += "   - For JIRA unbounded errors: \"Would you like tickets from the last 30 days, 90 days, or a specific time range?\"\n"
                synthesis_instruction += "   - For missing data: \"Could you provide [specific information needed]?\"\n"
                synthesis_instruction += "6. **Suggest alternatives**: What can the user do instead?\n"
                synthesis_instruction += "\n"
                synthesis_instruction += "**FORMAT**: Use friendly, conversational markdown (not JSON)\n"
                synthesis_instruction += "**TONE**: Helpful and apologetic, not technical or cold\n"
                synthesis_instruction += "**LENGTH**: 3-5 sentences minimum (NOT empty!)\n"
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
                # All succeeded - be concise and direct
                synthesis_instruction += "✅ **ANSWER THE USER'S QUESTION WITH CITATIONS**:\n"
                synthesis_instruction += "\n"
                synthesis_instruction += "🚫 **ABSOLUTELY FORBIDDEN RESPONSES**:\n"
                synthesis_instruction += "- ❌ DO NOT say: 'I can't produce the requested summary'\n"
                synthesis_instruction += "- ❌ DO NOT say: 'The block identifiers are not present'\n"
                synthesis_instruction += "- ❌ DO NOT say: 'I need to load the data'\n"
                synthesis_instruction += "- ❌ DO NOT make excuses or say you can't answer\n"
                synthesis_instruction += "- ❌ DO NOT explain what tools you used or the process\n"
                synthesis_instruction += "- ❌ DO NOT ask follow-up questions\n"
                synthesis_instruction += "\n"
                synthesis_instruction += "✅ **YOU MUST DO THIS**:\n"
                synthesis_instruction += "- The retrieval tool HAS PROVIDED the blocks with [R1-1] style identifiers\n"
                synthesis_instruction += "- Look at the tool results above - the block numbers are RIGHT THERE\n"
                synthesis_instruction += "- Provide a comprehensive, detailed answer using the retrieved data\n"
                synthesis_instruction += "- **MANDATORY**: Include inline citations [R1-1] IMMEDIATELY after EACH factual claim\n"
                synthesis_instruction += "- Focus on delivering the answer they asked for with proper citations\n"
                synthesis_instruction += "\n"
                synthesis_instruction += "**CITATION FORMAT (MANDATORY)**:\n"
                synthesis_instruction += "- Every factual claim from retrieved data MUST have [R1-1] style citation immediately after it\n"
                synthesis_instruction += "- Example CORRECT: 'Revenue grew 29% [R1-1]. Cash flows improved $142M [R1-2].'\n"
                synthesis_instruction += "- Example WRONG: 'Revenue grew 29%. Cash flows improved $142M. [R1-1][R1-2]'\n"
                synthesis_instruction += "- One citation per bracket: [R1-1][R2-3] NOT [R1-1, R2-3]\n"
                synthesis_instruction += "- Use block numbers exactly as shown in the tool results above\n"
                synthesis_instruction += "- The block numbers ARE in the tool results - use them!\n"

            synthesis_instruction += "\n**CRITICAL REQUIREMENTS**:\n"
            synthesis_instruction += "- Answer the user's original question directly with inline citations\n"
            synthesis_instruction += "- DO NOT mention tool names, execution status, or internal processes\n"
            synthesis_instruction += "- Every claim needs a citation [R1-1] right after it\n"
            synthesis_instruction += "- If data doesn't answer the question, say so briefly\n"
            synthesis_instruction += "\n**JSON FORMAT REQUIRED**:\n"
            synthesis_instruction += "You MUST respond with ONLY a valid JSON object in this exact format:\n"
            synthesis_instruction += '{\n'
            synthesis_instruction += '  "answer": "Your detailed answer with inline citations [R1-1] after each fact.",\n'
            synthesis_instruction += '  "reason": "How you derived the answer from the blocks",\n'
            synthesis_instruction += '  "confidence": "Very High | High | Medium | Low",\n'
            synthesis_instruction += '  "answerMatchType": "Derived From Chunks",\n'
            synthesis_instruction += '  "blockNumbers": ["R1-1", "R1-2", "R2-3"]\n'
            synthesis_instruction += '}\n'
            synthesis_instruction += "\n⚠️ CRITICAL: Include blockNumbers array with ALL cited block numbers.\n"
            synthesis_instruction += "Do NOT include 'citations' field - system handles that automatically.\n"
            synthesis_instruction += "Return ONLY the JSON object - no extra text."

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
            # Normal case - just add tool context with strong citation requirements
            json_format_instruction = "\n\n**⚠️ CRITICAL - CITATIONS ARE MANDATORY**:\n"
            json_format_instruction += "If you used internal knowledge/retrieval data, you MUST include inline citations [R1-1] immediately after EACH fact.\n"
            json_format_instruction += "\n"
            json_format_instruction += "**CITATION FORMAT**:\n"
            json_format_instruction += "- Put [R1-1] right after the claim: 'Revenue grew 29% [R1-1].'\n"
            json_format_instruction += "- One per bracket: [R1-1][R2-3] NOT [R1-1, R2-3]\n"
            json_format_instruction += "- List ALL cited blocks in blockNumbers array\n"
            json_format_instruction += "\n"
            json_format_instruction += "**JSON FORMAT REQUIRED**:\n"
            json_format_instruction += "You MUST respond with ONLY a valid JSON object:\n"
            json_format_instruction += '{\n'
            json_format_instruction += '  "answer": "Answer with inline citations [R1-1] after each fact.",\n'
            json_format_instruction += '  "reason": "How you derived the answer",\n'
            json_format_instruction += '  "confidence": "Very High | High | Medium | Low",\n'
            json_format_instruction += '  "answerMatchType": "Derived From Chunks",\n'
            json_format_instruction += '  "blockNumbers": ["R1-1", "R1-2"]\n'
            json_format_instruction += '}\n'
            json_format_instruction += "\nDo NOT include 'citations' field. Return ONLY the JSON object."

            if validated_messages and validated_messages[-1]["role"] == "user":
                validated_messages[-1]["content"] += f"\n\n{tool_context}{json_format_instruction}"
            else:
                validated_messages.append({
                    "role": "user",
                    "content": f"{tool_context}{json_format_instruction}\n\nProvide your response with citations."
                })

        # Get final results for citations
        final_results = state.get("final_results", [])
        # Ensure final_results is a list (might be stored as string or other format)
        if not isinstance(final_results, list):
            if isinstance(final_results, str):
                try:
                    final_results = json.loads(final_results)
                except (json.JSONDecodeError, TypeError):
                    logger.warning(f"⚠️ final_results is not a valid list or JSON: {type(final_results)}")
                    final_results = []
            else:
                logger.warning(f"⚠️ final_results is not a list: {type(final_results)}")
                final_results = []

        # CRITICAL DEBUG: Log final_results availability for citation processing
        logger.info(f"📊 CITATION DEBUG: final_results count = {len(final_results)}")
        if final_results:
            logger.debug(f"📊 First result sample: {list(final_results[0].keys()) if final_results else 'N/A'}")
        else:
            logger.error("⚠️ CRITICAL: final_results is EMPTY - citations will not be generated!")
            logger.error(f"⚠️ State keys available: {list(state.keys())}")
            logger.error(f"⚠️ virtual_record_id_to_result count: {len(state.get('virtual_record_id_to_result', {}))}")

        safe_stream_write(writer, {"event": "status", "data": {"status": "generating", "message": "Generating response..."}}, config)

        # Use stream_llm_response for new generation
        final_content = None

        try:
            virtual_record_id_to_result = state.get("virtual_record_id_to_result", {})
            tool_records = state.get("tool_records", [])
            logger.info("📦 CITATION DEBUG: Passing to stream_llm_response:")
            logger.info(f"   - final_results: {len(final_results)} items")
            logger.info(f"   - virtual_record_id_to_result: {len(virtual_record_id_to_result)} records")
            logger.info(f"   - tool_records: {len(tool_records)} records")
            async for stream_event in stream_llm_response(
                llm, validated_messages, final_results, logger,
                virtual_record_id_to_result=virtual_record_id_to_result,
                records=tool_records
            ):
                event_type = stream_event["event"]
                event_data = stream_event["data"]

                # Forward streaming events as-is
                # stream_llm_response already sends answer_chunk and complete events correctly
                safe_stream_write(writer, {"event": event_type, "data": event_data}, config)

                # Track the final complete data
                if event_type == "complete":
                    final_content = event_data

        except Exception as stream_error:
            logger.error(f"stream_llm_response failed: {stream_error}")

            # Fallback to direct LLM call
            try:
                response = await llm.ainvoke(validated_messages)
                fallback_content = response.content if hasattr(response, 'content') else str(response)

                # Process citations with tool records
                if final_results:
                    tool_records = state.get("tool_records", [])
                    virtual_record_id_to_result = state.get("virtual_record_id_to_result", {})
                    cited_fallback = process_citations(
                        fallback_content,
                        final_results,
                        records=tool_records,
                        from_agent=True,
                        virtual_record_id_to_result=virtual_record_id_to_result
                    )
                    if isinstance(cited_fallback, str):
                        fallback_content = cited_fallback
                    elif isinstance(cited_fallback, dict):
                        fallback_content = cited_fallback.get("answer", fallback_content)

                # Stream answer text only
                chunk_size = 100
                for i in range(0, len(fallback_content), chunk_size):
                    chunk = fallback_content[i:i + chunk_size]
                    safe_stream_write(writer, {"event": "answer_chunk", "data": {"chunk": chunk}}, config)
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

                safe_stream_write(writer, {"event": "complete", "data": completion_data}, config)
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
                safe_stream_write(writer, {"event": "answer_chunk", "data": {"chunk": error_content}}, config)
                safe_stream_write(writer, {"event": "complete", "data": error_response}, config)
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

                # FALLBACK: Generate a helpful error message for the user
                if response_len == 0 and failed_count > 0:
                    logger.warning("🔧 Generating fallback response for empty output with failures")
                    fallback_msg = "I apologize, but I encountered an issue while processing your request.\n\n"

                    # Summarize what went wrong
                    if all_tool_results:
                        first_error = None
                        for result in all_tool_results:
                            if result.get("status") == "error":
                                first_error = result
                                break

                        if first_error:
                            error_msg = str(first_error.get("result", {})).get("error", "unknown error") if isinstance(first_error.get("result"), dict) else str(first_error.get("result", ""))[:200]

                            if "unbounded" in error_msg.lower():
                                fallback_msg += "I tried to search for your tickets, but JIRA requires a time range for the query.\n\n"
                                fallback_msg += "**Would you like to see:**\n"
                                fallback_msg += "- Tickets from the last 30 days?\n"
                                fallback_msg += "- Tickets from the last 90 days?\n"
                                fallback_msg += "- Tickets from a specific date range?\n\n"
                                fallback_msg += "Please let me know and I'll retrieve them for you."
                            else:
                                fallback_msg += f"The error was: {error_msg[:150]}\n\n"
                                fallback_msg += "Could you provide more details or try rephrasing your request?"
                    else:
                        fallback_msg += "I wasn't able to complete the task. Could you provide more details or try again?"

                    answer_text = fallback_msg
                    state["response"] = answer_text
                    logger.info(f"✅ Using fallback response: {len(answer_text)} characters")
        else:
            logger.error("❌ No final content generated - this should not happen")
            # FALLBACK: Generate generic error message
            fallback_msg = "I apologize, but I encountered an unexpected error while processing your request. Could you please try again or rephrase your question?"
            state["response"] = fallback_msg
            answer_text = fallback_msg
            logger.warning(f"🔧 Using generic fallback response: {len(answer_text)} characters")

        # ⚡ PERFORMANCE: Finish step timing and log complete summary
        duration = perf.finish_step(response_length=len(answer_text) if final_content else 0)
        logger.debug(f"⚡ final_response_node completed in {duration:.0f}ms")

        # ⚡ PERFORMANCE SUMMARY: Log complete performance report
        perf.log_summary(logger)

        # Store performance summary in state for API response
        state["performance_summary"] = perf.get_summary()

        return state

    except Exception as e:
        logger.error(f"Error in agent final response: {str(e)}", exc_info=True)
        perf.finish_step(error=True)
        perf.log_summary(logger)  # Still log performance even on error
        state["error"] = {"status_code": 400, "detail": str(e)}
        safe_stream_write(writer, {"event": "error", "data": {"error": str(e)}}, config)
        return state


# Helper function to normalize response
def _normalize_response_format(response) -> dict:
    """Normalize response to expected format - handle both string and dict responses"""
    if isinstance(response, str):
        # Try to parse if it looks like JSON (including markdown code blocks)
        response_stripped = response.strip()

        # Check if it's wrapped in markdown code blocks
        if "```json" in response_stripped or "```" in response_stripped:
            try:
                # Use extract_json_from_string to handle markdown code blocks
                parsed = extract_json_from_string(response_stripped)
                if isinstance(parsed, dict) and "answer" in parsed:
                    return {
                        "answer": parsed.get("answer", ""),
                        "citations": [],  # NEVER use LLM-generated citations - normalization will create proper ones
                        "confidence": parsed.get("confidence", "High"),
                        "reason": parsed.get("reason", "Direct response"),
                        "answerMatchType": parsed.get("answerMatchType", "Derived From Tool Execution"),
                        "chunkIndexes": parsed.get("chunkIndexes", []),
                        "workflowSteps": parsed.get("workflowSteps", []),
                        "blockNumbers": parsed.get("blockNumbers", [])
                    }
            except (ValueError, json.JSONDecodeError):
                # If extraction fails, try regular JSON parsing
                pass

        # Try regular JSON parsing
        if response_stripped.startswith('{') or response_stripped.startswith('['):
            try:
                parsed = json.loads(response_stripped)
                if isinstance(parsed, dict) and "answer" in parsed:
                    return {
                        "answer": parsed.get("answer", ""),
                        "citations": [],  # NEVER use LLM-generated citations - normalization will create proper ones
                        "confidence": parsed.get("confidence", "High"),
                        "reason": parsed.get("reason", "Direct response"),
                        "answerMatchType": parsed.get("answerMatchType", "Derived From Tool Execution"),
                        "chunkIndexes": parsed.get("chunkIndexes", []),
                        "workflowSteps": parsed.get("workflowSteps", []),
                        "blockNumbers": parsed.get("blockNumbers", [])
                    }
            except (ValueError, json.JSONDecodeError):
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
            "citations": [],  # NEVER use LLM-generated citations - normalization will create proper ones
            "confidence": response.get("confidence", "Medium"),
            "reason": response.get("reason", "Processed response"),
            "answerMatchType": response.get("answerMatchType", "Derived From Tool Execution"),
            "chunkIndexes": response.get("chunkIndexes", []),
            "workflowSteps": response.get("workflowSteps", []),
            "blockNumbers": response.get("blockNumbers", [])
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

def _validate_and_fix_message_sequence(messages) -> List[Any]:
    """
    Validate and fix message sequence to ensure proper tool_call threading.

    Standard LLM API Requirements (OpenAI, Anthropic, Gemini, etc.):
    1. ToolMessages MUST have a preceding AIMessage with tool_calls
    2. AIMessages with tool_calls MUST have ALL corresponding ToolMessages
    3. Tool_call_id in ToolMessage MUST match id in AIMessage.tool_calls

    This validation works across all LLM providers that support tool calling.
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


def _clean_message_history(messages, is_complex: bool = False) -> List[Any]:
    """Clean message history with context length management

    Args:
        messages: List of messages to clean
        is_complex: Whether this is a complex query (uses larger history limit)
    """
    # CRITICAL: Always validate message sequence to prevent orphaned ToolMessages
    # Even small message lists can have invalid tool_call/response pairs after optimization
    validated_messages = _validate_and_fix_message_sequence(messages)
    cleaned = []

    # Keep system message (first message)
    if validated_messages and isinstance(validated_messages[0], SystemMessage):
        cleaned.append(validated_messages[0])

    # ⚡ NUCLEAR: Use aggressive limit for simple queries (75% faster LLM!)
    message_limit = MAX_MESSAGES_HISTORY if is_complex else MAX_MESSAGES_HISTORY_SIMPLE

    # Keep last N messages to manage context length
    recent_messages = validated_messages[1:] if validated_messages else []

    if len(recent_messages) > message_limit:
        # Keep the most recent messages
        recent_messages = recent_messages[-message_limit:]
        if not is_complex and len(validated_messages) > message_limit:
            # Log when we're using aggressive reduction
            logger = logging.getLogger(__name__)
            logger.debug(f"⚡ NUCLEAR: Aggressive context reduction - {len(validated_messages)} → {message_limit} messages (simple query optimization)")

    # Process recent messages and ALWAYS summarize oversized tool results
    for i, msg in enumerate(recent_messages):
        if isinstance(msg, (SystemMessage, HumanMessage, AIMessage)):
            cleaned.append(msg)
        elif hasattr(msg, 'tool_call_id'):
            # ⚡ PERFORMANCE: Always summarize tool results that exceed MAX_TOOL_RESULT_LENGTH
            # This prevents massive context bloat and reduces LLM latency
            msg_content = msg.content if hasattr(msg, 'content') else str(msg)
            if len(msg_content) > MAX_TOOL_RESULT_LENGTH:
                summarized_msg = _summarize_tool_result(msg)
                if summarized_msg:
                    cleaned.append(summarized_msg)
                else:
                    # Fallback: truncate if summarization fails
                    truncated_content = msg_content[:MAX_TOOL_RESULT_LENGTH] + "\n...[truncated for brevity]"
                    truncated_msg = ToolMessage(content=truncated_content, tool_call_id=msg.tool_call_id)
                    cleaned.append(truncated_msg)
            else:
                # Keep full tool results for reasonably sized results
                cleaned.append(msg)

    return cleaned


def _summarize_tool_result(tool_result_msg) -> Optional[object]:
    """Summarize tool results to reduce context length WHILE PRESERVING CRITICAL DATA"""
    try:
        from langchain_core.messages import ToolMessage

        # Extract tool result content
        if hasattr(tool_result_msg, 'content'):
            content = tool_result_msg.content
        else:
            content = str(tool_result_msg)

        # If content is too long, intelligently summarize
        if len(content) > MAX_TOOL_RESULT_LENGTH:
            # Try to extract key information
            if isinstance(content, str):
                # For JSON responses, try to intelligently summarize
                try:
                    import json
                    data = json.loads(content)
                    if isinstance(data, dict):
                        # ⚡ SMART SUMMARIZATION: Preserve structure but limit array lengths
                        summarized_data = _smart_summarize_dict(data, max_depth=3, max_array_items=5)

                        # Convert back to JSON
                        summary_content = json.dumps(summarized_data, indent=2)

                        # If still too long after smart summarization, truncate
                        if len(summary_content) > MAX_TOOL_RESULT_LENGTH:
                            summary_content = summary_content[:MAX_TOOL_RESULT_LENGTH] + "\n... [TRUNCATED - full data available to agent]"

                        content = summary_content
                    else:
                        # Not a dict - just truncate
                        content = content[:MAX_TOOL_RESULT_LENGTH] + "\n... [TRUNCATED]"
                except (json.JSONDecodeError, TypeError):
                    # Not JSON - just truncate
                    content = content[:MAX_TOOL_RESULT_LENGTH] + "\n... [TRUNCATED]"
            else:
                content = str(content)[:MAX_TOOL_RESULT_LENGTH] + "\n... [TRUNCATED]"

        # Create summarized tool message
        return ToolMessage(
            content=content,
            tool_call_id=tool_result_msg.tool_call_id
        )

    except Exception:
        # If summarization fails, return truncated original
        try:
            from langchain_core.messages import ToolMessage
            content = str(tool_result_msg.content)[:1500] + "\n... [ERROR IN SUMMARIZATION - TRUNCATED]"
            return ToolMessage(
                content=content,
                tool_call_id=tool_result_msg.tool_call_id
            )
        except Exception:
            return None


def _smart_summarize_dict(data: dict, max_depth: int = 3, max_array_items: int = 5, current_depth: int = 0) -> dict:
    """
    Intelligently summarize a dictionary by:
    - Preserving structure and keys
    - Limiting array lengths to first N items + count
    - Maintaining nested structure up to max_depth
    - Keeping success/error indicators
    """
    if current_depth >= max_depth:
        return {"[...]": "nested data truncated"}

    result = {}
    for key, value in data.items():
        if isinstance(value, dict):
            # Recursively summarize nested dicts
            result[key] = _smart_summarize_dict(value, max_depth, max_array_items, current_depth + 1)
        elif isinstance(value, list):
            if len(value) > max_array_items:
                # Keep first N items + add count
                result[key] = value[:max_array_items]
                result[f"{key}_total_count"] = len(value)
                result[f"{key}_note"] = f"Showing first {max_array_items} of {len(value)} items"
            else:
                # Keep full list if small enough
                result[key] = value
        else:
            # Keep primitive values as-is
            result[key] = value

    return result


# ============================================================================
# ROUTING FUNCTIONS
# ============================================================================

def should_continue(state: ChatState) -> Literal["execute_tools", "final"]:
    """Route based on tool calls"""
    return "execute_tools" if state.get("pending_tool_calls", False) else "final"


def check_for_error(state: ChatState) -> Literal["error", "continue"]:
    """Check for errors"""
    return "error" if state.get("error") else "continue"
