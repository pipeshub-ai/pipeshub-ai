"""
Agent Node Implementations

Enterprise-grade agent system with:
- Reliable cascading tool execution
- Smart placeholder resolution
- Robust error handling and recovery
- Accurate tool result processing
- Context-aware conversation handling
- Comprehensive logging and debugging
"""


import asyncio
import functools
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter

from app.modules.agents.qna.chat_state import ChatState
from app.modules.agents.qna.stream_utils import safe_stream_write
from app.modules.qna.response_prompt import create_response_messages
from app.utils.streaming import stream_llm_response

# ============================================================================
# CONSTANTS & CONFIGURATION
# ============================================================================

# Logging
logger = logging.getLogger(__name__)

# Tool execution constants
TOOL_RESULT_TUPLE_LENGTH = 2
MAX_PARALLEL_TOOLS = 10
TOOL_TIMEOUT_SECONDS = 60.0
RETRIEVAL_TIMEOUT_SECONDS = 45.0  # Faster timeout for retrieval

# Response formatting constants
USER_QUERY_MAX_LENGTH = 300
BOT_RESPONSE_MAX_LENGTH = 800
MAX_TOOL_RESULT_PREVIEW_LENGTH = 500
MAX_AVAILABLE_TOOLS_DISPLAY = 20

# Opik tracer initialization
_opik_tracer = None
_opik_api_key = os.getenv("OPIK_API_KEY")
_opik_workspace = os.getenv("OPIK_WORKSPACE")
if _opik_api_key and _opik_workspace:
    try:
        from opik.integrations.langchain import OpikTracer
        _opik_tracer = OpikTracer()
        logger.info("✅ Opik tracer initialized")
    except Exception as e:
        logger.warning(f"⚠️ Failed to initialize Opik tracer: {e}")


# ============================================================================
# CONFIGURATION CLASS
# ============================================================================

class NodeConfig:
    """Centralized node behavior configuration"""
    MAX_PARALLEL_TOOLS: int = 10
    TOOL_TIMEOUT_SECONDS: float = 60.0
    RETRIEVAL_TIMEOUT_SECONDS: float = 45.0  # Faster timeout for retrieval
    PLANNER_TIMEOUT_SECONDS: float = 20.0
    REFLECTION_TIMEOUT_SECONDS: float = 8.0

    # Retry & iteration limits
    MAX_RETRIES: int = 1
    MAX_ITERATIONS: int = 3
    MAX_VALIDATION_RETRIES: int = 2

    # Query limits
    MAX_RETRIEVAL_QUERIES: int = 3
    MAX_QUERY_LENGTH: int = 100
    MAX_QUERY_WORDS: int = 8


# ============================================================================
# RESULT CLEANING UTILITIES
# ============================================================================

REMOVE_FIELDS = {
    "self", "_links", "_embedded", "_meta", "_metadata",
    "expand", "expansions", "schema", "$schema",
    "avatarUrls", "avatarUrl", "iconUrl", "iconUri", "thumbnailUrl",
    "avatar", "icon", "thumbnail", "profilePicture",
    "nextPageToken", "prevPageToken", "pageToken",
    "cursor", "offset", "pagination",
    "startAt", "maxResults", "total",
    "trace", "traceId", "requestId", "correlationId",
    "debug", "debugInfo", "stack", "stackTrace",
    "headers", "cookies", "request", "response",
    "httpVersion", "protocol", "encoding",
    "timeZone", "timezone", "locale", "language",
    "accountType", "active", "properties",
    "hierarchyLevel", "subtask", "avatarId",
    "watches", "votes", "watchers", "voters",
    "changelog", "history", "worklog", "worklogs",
}


def clean_tool_result(result: object) -> object:
    """Clean tool result by removing verbose fields"""
    if isinstance(result, tuple) and len(result) == TOOL_RESULT_TUPLE_LENGTH:
        success, data = result
        return (success, clean_tool_result(data))

    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            cleaned = clean_tool_result(parsed)
            return json.dumps(cleaned, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            return result

    if isinstance(result, dict):
        cleaned = {}
        for key, value in result.items():
            if key in REMOVE_FIELDS or key.lower() in REMOVE_FIELDS:
                continue
            if key.startswith("_") or key.startswith("$"):
                continue

            if isinstance(value, dict):
                cleaned_value = clean_tool_result(value)
                if cleaned_value:
                    cleaned[key] = cleaned_value
            elif isinstance(value, list):
                cleaned[key] = [clean_tool_result(item) for item in value]
            else:
                cleaned[key] = value
        return cleaned

    if isinstance(result, list):
        return [clean_tool_result(item) for item in result]

    return result


def format_result_for_llm(result: object, tool_name: str = "") -> str:
    """Format result for LLM consumption"""
    if isinstance(result, tuple) and len(result) == TOOL_RESULT_TUPLE_LENGTH:
        success, data = result
        status = "✅ Success" if success else "❌ Failed"
        content = format_result_for_llm(data, tool_name)
        return f"{status}\n{content}"

    if isinstance(result, (dict, list)):
        try:
            return json.dumps(result, indent=2, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(result)

    return str(result)


# ============================================================================
# TOOL RESULT PROCESSING - RELIABLE EXTRACTION
# ============================================================================

class ToolResultExtractor:
    """Reliable extraction of data from tool results"""

    @staticmethod
    def extract_success_status(result: Any) -> bool:
        """
        Reliably detect if a tool execution succeeded.

        Handles multiple result formats:
        - Tuple: (bool, data)
        - Dict: {"success": bool, ...}
        - String: checks for error indicators
        """
        if result is None:
            return False

        # Tuple format: (success, data)
        if isinstance(result, tuple) and len(result) >= 1:
            if isinstance(result[0], bool):
                return result[0]

        # Dict format
        if isinstance(result, dict):
            # Check success field
            if "success" in result and isinstance(result["success"], bool):
                return result["success"]
            # Check ok field
            if "ok" in result and isinstance(result["ok"], bool):
                return result["ok"]
            # Check for error field
            if "error" in result and result["error"] not in (None, "", "null"):
                return False

        # String format - check for error indicators
        result_str = str(result).lower()
        error_indicators = [
            "error:", '"error": "', "'error': '",
            "failed", "failure", "exception",
            "traceback", "status_code: 4", "status_code: 5"
        ]

        # Ignore null errors
        if '"error": null' in result_str or "'error': none" in result_str:
            return True

        return not any(ind in result_str for ind in error_indicators)

    @staticmethod
    def extract_data_from_result(result: Any) -> Any:
        """
        Extract the actual data from a tool result.

        Handles:
        - Tuple: (success, data) → returns data
        - Dict: tries to parse JSON strings
        - Other: returns as-is
        """
        # Handle tuple format
        if isinstance(result, tuple) and len(result) == TOOL_RESULT_TUPLE_LENGTH:
            _, data = result
            return ToolResultExtractor.extract_data_from_result(data)

        # Handle JSON strings
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
                return parsed
            except (json.JSONDecodeError, TypeError):
                # ✅ NEW: Return the string directly (for retrieval tool)
                # Retrieval returns formatted string, not JSON
                return result

        return result

    @staticmethod
    def extract_field_from_data(data: Any, field_path: List[str]) -> Optional[Any]:
        """
        Extract a specific field from data using a field path.

        Examples:
        - ["data", "key"] → data.key
        - ["data", "results", "0", "id"] → data.results[0].id

        Handles:
        - Nested dicts
        - Arrays (auto-extracts from first item)
        - JSON strings
        """
        current = data

        for field in field_path:
            if current is None:
                return None

            # Handle dict
            if isinstance(current, dict):
                current = current.get(field)

                # If we got an array, use first item for next navigation
                if isinstance(current, list) and len(current) > 0:
                    current = current[0]

            # Handle array with index
            elif isinstance(current, list):
                try:
                    # Try to parse as index
                    index = int(field)
                    if 0 <= index < len(current):
                        current = current[index]
                    else:
                        return None
                except ValueError:
                    # Not an index, try to get field from first item
                    if len(current) > 0 and isinstance(current[0], dict):
                        current = current[0].get(field)
                    else:
                        return None

            # Handle JSON string
            elif isinstance(current, str):
                try:
                    parsed = json.loads(current)
                    if isinstance(parsed, dict):
                        current = parsed.get(field)
                    else:
                        return None
                except (json.JSONDecodeError, TypeError):
                    return None
            else:
                return None

        return current


# ============================================================================
# PLACEHOLDER RESOLUTION - SIMPLIFIED & RELIABLE
# ============================================================================

class PlaceholderResolver:
    """
    Simplified placeholder resolution for cascading tools.

    Supports formats:
    - {{tool_name.field}} → single field
    - {{tool_name.data.key}} → nested field
    - {{tool_name.results.0.id}} → array index
    """

    PLACEHOLDER_PATTERN = re.compile(r'\{\{([^}]+)\}\}')

    @classmethod
    def has_placeholders(cls, args: Dict[str, Any]) -> bool:
        """Check if args contain any placeholders"""
        args_str = json.dumps(args, default=str)
        return bool(cls.PLACEHOLDER_PATTERN.search(args_str))

    @classmethod
    def resolve_all(
        cls,
        args: Dict[str, Any],
        results_by_tool: Dict[str, Any],
        log: logging.Logger
    ) -> Dict[str, Any]:
        """
        Resolve all placeholders in args using results from previous tools.

        Returns:
            New dict with all placeholders resolved
        """
        resolved = {}

        for key, value in args.items():
            if isinstance(value, str) and '{{' in value:
                resolved[key] = cls._resolve_string_value(value, results_by_tool, log)
            elif isinstance(value, dict):
                resolved[key] = cls.resolve_all(value, results_by_tool, log)
            elif isinstance(value, list):
                resolved[key] = [
                    cls.resolve_all(item, results_by_tool, log) if isinstance(item, dict)
                    else cls._resolve_string_value(item, results_by_tool, log) if isinstance(item, str) and '{{' in item
                    else item
                    for item in value
                ]
            else:
                resolved[key] = value

        return resolved

    @classmethod
    def _resolve_string_value(
        cls,
        value: str,
        results_by_tool: Dict[str, Any],
        log: logging.Logger
    ) -> str:
        """Resolve all placeholders in a string value"""
        matches = cls.PLACEHOLDER_PATTERN.findall(value)
        resolved_value = value

        for match in matches:
            replacement = cls._resolve_single_placeholder(match, results_by_tool, log)
            if replacement is not None:
                placeholder_full = f"{{{{{match}}}}}"
                resolved_value = resolved_value.replace(placeholder_full, str(replacement))
            else:
                log.warning(f"⚠️ Could not resolve placeholder: {{{{{match}}}}}")

        return resolved_value

    @classmethod
    def _resolve_single_placeholder(
        cls,
        placeholder: str,
        results_by_tool: Dict[str, Any],
        log: logging.Logger
    ) -> Optional[Any]:
        """
        Resolve a single placeholder to its value.

        Args:
            placeholder: e.g., "jira.create_issue.data.key"
            results_by_tool: {"jira.create_issue": {...}}

        Returns:
            Extracted value or None if not found
        """
        # Parse placeholder into tool_name and field_path
        tool_name, field_path = cls._parse_placeholder(placeholder, results_by_tool)

        if not tool_name or tool_name not in results_by_tool:
            log.debug(f"Tool not found for placeholder: {placeholder}")
            return None

        tool_data = results_by_tool[tool_name]

        # ✅ SPECIAL CASE: Retrieval tool returns string directly
        if "retrieval" in tool_name.lower() and isinstance(tool_data, str):
            # If placeholder is just the tool name (no field path), return the string
            if not field_path or field_path == ['data']:
                log.info(f"✅ Resolved {{{{{placeholder}}}}} → [retrieval content string]")
                return tool_data
            # If they're trying to access a field, we can't (it's a string)
            log.warning(f"❌ Retrieval tool returns string, cannot access field: {field_path}")
            return None

        # Extract data using field path (for structured results)
        extracted = ToolResultExtractor.extract_field_from_data(tool_data, field_path)

        if extracted is not None:
            log.info(f"✅ Resolved {{{{{placeholder}}}}} → {str(extracted)[:50]}...")
        else:
            log.warning(f"❌ Could not extract field from placeholder: {{{{{placeholder}}}}}")
            log.debug(f"Available data: {str(tool_data)[:200]}")

        return extracted

    @classmethod
    def _parse_placeholder(
        cls,
        placeholder: str,
        results_by_tool: Dict[str, Any]
    ) -> Tuple[Optional[str], List[str]]:
        """
        Parse placeholder into tool_name and field_path.

        Examples:
        - "jira.create_issue.data.key" → ("jira.create_issue", ["data", "key"])
        - "create_issue.key" → ("jira.create_issue", ["key"]) if fuzzy match

        Returns:
            (tool_name, field_path) or (None, []) if can't parse
        """
        # Try exact match first (longest tool names first)
        sorted_tools = sorted(results_by_tool.keys(), key=len, reverse=True)

        for tool_name in sorted_tools:
            if placeholder.startswith(tool_name + '.'):
                # Extract field path
                remaining = placeholder[len(tool_name) + 1:]
                field_path = remaining.split('.') if remaining else []
                return tool_name, field_path

        # Fuzzy match - find tool that matches end of placeholder
        parts = placeholder.split('.')
        if len(parts) >= 2:
            # Try matching the first part against tool names
            prefix = parts[0]
            field_path = parts[1:]

            for tool_name in sorted_tools:
                # Normalize for comparison
                normalized_tool = tool_name.lower().replace('_', '').replace('.', '')
                normalized_prefix = prefix.lower().replace('_', '')

                if normalized_prefix in normalized_tool or normalized_tool.endswith(normalized_prefix):
                    return tool_name, field_path

        return None, []


# ============================================================================
# TOOL EXECUTION - SEQUENTIAL WITH CASCADING SUPPORT
# ============================================================================

class ToolExecutor:
    """Handles tool execution with cascading support"""

    @staticmethod
    async def execute_tools(
        planned_tools: List[Dict[str, Any]],
        tools_by_name: Dict[str, Any],
        llm: Any,
        state: ChatState,
        log: logging.Logger,
        writer: StreamWriter,
        config: RunnableConfig
    ) -> List[Dict[str, Any]]:
        """
        Execute tools - sequentially if cascading, parallel otherwise.

        Returns:
            List of tool results with status, result, tool_name, etc.
        """
        # Detect if we need sequential execution (cascading)
        has_cascading = PlaceholderResolver.has_placeholders(
            {"tools": planned_tools}
        )

        if has_cascading:
            log.info("🔗 Cascading detected - executing sequentially")
            return await ToolExecutor._execute_sequential(
                planned_tools, tools_by_name, llm, state, log, writer, config
            )
        else:
            log.info("⚡ No cascading - executing in parallel")
            return await ToolExecutor._execute_parallel(
                planned_tools, tools_by_name, llm, state, log
            )

    @staticmethod
    async def _execute_sequential(
        planned_tools: List[Dict[str, Any]],
        tools_by_name: Dict[str, Any],
        llm: Any,
        state: ChatState,
        log: logging.Logger,
        writer: StreamWriter,
        config: RunnableConfig
    ) -> List[Dict[str, Any]]:
        """Execute tools sequentially with placeholder resolution"""
        from app.modules.agents.qna.tool_system import _sanitize_tool_name_if_needed

        tool_results = []
        results_by_tool = {}  # Store successful results for placeholder resolution

        for i, tool_call in enumerate(planned_tools):
            tool_name = tool_call.get("name", "")
            tool_args = tool_call.get("args", {})

            # Normalize tool name
            normalized_name = _sanitize_tool_name_if_needed(tool_name, llm) if llm else tool_name
            actual_tool_name = normalized_name if normalized_name in tools_by_name else tool_name

            if actual_tool_name not in tools_by_name:
                log.warning(f"❌ Tool not found: {tool_name}")
                tool_results.append({
                    "tool_name": tool_name,
                    "result": f"Error: Tool '{tool_name}' not found",
                    "status": "error",
                    "tool_id": f"call_{i}_{tool_name}"
                })
                continue

            # Resolve placeholders
            resolved_args = PlaceholderResolver.resolve_all(tool_args, results_by_tool, log)

            # Check for unresolved placeholders
            if PlaceholderResolver.has_placeholders(resolved_args):
                unresolved = PlaceholderResolver.PLACEHOLDER_PATTERN.findall(json.dumps(resolved_args))
                log.error(f"❌ Unresolved placeholders in {actual_tool_name}: {unresolved}")
                tool_results.append({
                    "tool_name": actual_tool_name,
                    "result": f"Error: Unresolved placeholders: {', '.join(set(unresolved))}",
                    "status": "error",
                    "tool_id": f"call_{i}_{actual_tool_name}"
                })
                continue

            # Stream status
            safe_stream_write(writer, {
                "event": "status",
                "data": {"status": "executing", "message": f"Executing {actual_tool_name}..."}
            }, config)

            # Execute tool
            result_dict = await ToolExecutor._execute_single_tool(
                tool=tools_by_name[actual_tool_name],
                tool_name=actual_tool_name,
                tool_args=resolved_args,
                tool_id=f"call_{i}_{actual_tool_name}",
                state=state,
                log=log
            )

            tool_results.append(result_dict)

            # Store successful results for next placeholder resolution
            if result_dict.get("status") == "success":
                # Extract clean data for placeholder resolution
                result_data = ToolResultExtractor.extract_data_from_result(
                    result_dict.get("result")
                )
                results_by_tool[actual_tool_name] = result_data
                log.debug(f"✅ Stored result for {actual_tool_name} (keys: {list(result_data.keys()) if isinstance(result_data, dict) else type(result_data).__name__})")
            else:
                log.debug(f"❌ Skipped storing failed tool: {actual_tool_name}")

        return tool_results

    @staticmethod
    async def _execute_parallel(
        planned_tools: List[Dict[str, Any]],
        tools_by_name: Dict[str, Any],
        llm: Any,
        state: ChatState,
        log: logging.Logger
    ) -> List[Dict[str, Any]]:
        """Execute tools in parallel"""
        from app.modules.agents.qna.tool_system import _sanitize_tool_name_if_needed

        tasks = []

        for i, tool_call in enumerate(planned_tools[:NodeConfig.MAX_PARALLEL_TOOLS]):
            tool_name = tool_call.get("name", "")
            tool_args = tool_call.get("args", {})

            # Normalize tool name
            normalized_name = _sanitize_tool_name_if_needed(tool_name, llm) if llm else tool_name
            actual_tool_name = normalized_name if normalized_name in tools_by_name else tool_name

            if actual_tool_name not in tools_by_name:
                log.warning(f"❌ Tool not found: {tool_name}")
                # Create error result directly
                tasks.append(asyncio.create_task(asyncio.sleep(0, result={
                    "tool_name": tool_name,
                    "result": f"Error: Tool '{tool_name}' not found",
                    "status": "error",
                    "tool_id": f"call_{i}_{tool_name}"
                })))
                continue

            tasks.append(
                ToolExecutor._execute_single_tool(
                    tool=tools_by_name[actual_tool_name],
                    tool_name=actual_tool_name,
                    tool_args=tool_args,
                    tool_id=f"call_{i}_{actual_tool_name}",
                    state=state,
                    log=log
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter and process results
        tool_results = []
        for result in results:
            if isinstance(result, Exception):
                log.error(f"❌ Tool execution exception: {result}")
                continue
            if isinstance(result, dict):
                tool_results.append(result)

        return tool_results

    @staticmethod
    async def _execute_single_tool(
        tool: object,
        tool_name: str,
        tool_args: Dict[str, Any],
        tool_id: str,
        state: ChatState,
        log: logging.Logger
    ) -> Dict[str, Any]:
        """
        Execute a single tool with proper timeout and error handling.

        Returns:
            Dict with: tool_name, result, status, tool_id, args, duration_ms
        """
        start_time = time.perf_counter()

        try:
            # Normalize args
            if isinstance(tool_args, dict) and "kwargs" in tool_args and len(tool_args) == 1:
                tool_args = tool_args["kwargs"]

            log.debug(f"⚙️ Executing {tool_name} with args: {json.dumps(tool_args, default=str)[:150]}...")

            # Validate args using Pydantic schema
            validated_args = await ToolExecutor._validate_and_normalize_args(
                tool, tool_name, tool_args, log
            )

            if validated_args is None:
                # Validation failed - error already logged
                duration_ms = (time.perf_counter() - start_time) * 1000
                return {
                    "tool_name": tool_name,
                    "result": "Error: Argument validation failed",
                    "status": "error",
                    "tool_id": tool_id,
                    "args": tool_args,
                    "duration_ms": duration_ms
                }

            # Determine timeout based on tool type
            timeout = NodeConfig.TOOL_TIMEOUT_SECONDS
            if "retrieval" in tool_name.lower():
                timeout = NodeConfig.RETRIEVAL_TIMEOUT_SECONDS

            # Execute tool
            result = await asyncio.wait_for(
                ToolExecutor._run_tool(tool, validated_args),
                timeout=timeout
            )

            # Process result
            success = ToolResultExtractor.extract_success_status(result)

            # Handle retrieval output
            if "retrieval" in tool_name.lower():
                content = ToolExecutor._process_retrieval_output(result, state, log)
            else:
                content = clean_tool_result(result)

            duration_ms = (time.perf_counter() - start_time) * 1000
            status = "success" if success else "error"

            log.info(f"{'✅' if success else '❌'} {tool_name}: {duration_ms:.0f}ms")

            return {
                "tool_name": tool_name,
                "result": content,
                "status": status,
                "tool_id": tool_id,
                "args": tool_args,
                "duration_ms": duration_ms
            }

        except asyncio.TimeoutError:
            duration_ms = (time.perf_counter() - start_time) * 1000
            error_msg = f"Timeout after {duration_ms:.0f}ms"
            if "retrieval" in tool_name.lower():
                error_msg = "Search timed out - query may be too complex. Try simpler query."

            log.error(f"⏱️ {tool_name} timed out after {duration_ms:.0f}ms")
            return {
                "tool_name": tool_name,
                "result": f"Error: {error_msg}",
                "status": "error",
                "tool_id": tool_id,
                "args": tool_args,
                "duration_ms": duration_ms
            }

        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log.error(f"💥 {tool_name} failed: {e}", exc_info=True)
            return {
                "tool_name": tool_name,
                "result": f"Error: {type(e).__name__}: {str(e)}",
                "status": "error",
                "tool_id": tool_id,
                "args": tool_args,
                "duration_ms": duration_ms
            }

    @staticmethod
    async def _validate_and_normalize_args(
        tool: object,
        tool_name: str,
        tool_args: Dict[str, Any],
        log: logging.Logger
    ) -> Optional[Dict[str, Any]]:
        """Validate and normalize tool args using Pydantic schema"""
        try:
            # Get schema
            args_schema = getattr(tool, 'args_schema', None)
            if not args_schema:
                return tool_args  # No validation available

            # Validate
            validated_model = args_schema.model_validate(tool_args)
            validated_args = validated_model.model_dump(exclude_unset=True)

            log.debug(f"✅ Validated args for {tool_name}")
            return validated_args

        except Exception as e:
            log.error(f"❌ Validation failed for {tool_name}: {e}")
            return None

    @staticmethod
    async def _run_tool(tool: object, args: Dict[str, Any]) -> Any:
        """Run tool using appropriate method"""
        if hasattr(tool, 'arun'):
            return await tool.arun(args)
        elif hasattr(tool, '_run'):
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, functools.partial(tool._run, **args))
        else:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, functools.partial(tool.run, **args))

    @staticmethod
    def _process_retrieval_output(result: Any, state: ChatState, log: logging.Logger) -> str:
        """Process retrieval tool output and update state"""
        try:
            from app.agents.actions.retrieval.retrieval import RetrievalToolOutput

            # Try to parse as RetrievalToolOutput
            retrieval_output = None

            if isinstance(result, dict) and "content" in result and "final_results" in result:
                retrieval_output = RetrievalToolOutput(**result)
            elif isinstance(result, str):
                try:
                    data = json.loads(result)
                    if isinstance(data, dict) and "content" in data and "final_results" in data:
                        retrieval_output = RetrievalToolOutput(**data)
                except (json.JSONDecodeError, TypeError):
                    pass

            if retrieval_output:
                state["final_results"] = retrieval_output.final_results
                state["virtual_record_id_to_result"] = retrieval_output.virtual_record_id_to_result

                if retrieval_output.virtual_record_id_to_result:
                    state["tool_records"] = list(retrieval_output.virtual_record_id_to_result.values())

                log.info(f"📚 Retrieved {len(retrieval_output.final_results)} knowledge blocks")
                return retrieval_output.content

        except Exception as e:
            log.warning(f"⚠️ Could not process retrieval output: {e}")

        return str(result)


# ============================================================================
# PART 2: PLANNER NODE + REFLECTION + HELPER FUNCTIONS
# ============================================================================

# ============================================================================
# PLANNER PROMPTS - IMPROVED FOR ACCURACY
# ============================================================================

JIRA_GUIDANCE = r"""
## JIRA-Specific Guidance

### Never Fabricate Data
- ❌ NEVER invent emails, accountIds, or user identifiers
- ✅ Use `jira.search_users(query="[USER_EMAIL]")` to get accountIds
- ✅ Use project keys from Reference Data

### JQL Syntax Rules
1. Unresolved: `resolution IS EMPTY` (NOT `resolution = Unresolved`)
2. Current user: `currentUser()` with parentheses
3. Empty fields: `IS EMPTY` or `IS NULL`
4. Text values: Use quotes: `status = "Open"`
5. Assignee: Get accountId from `jira.search_users()`, then use in JQL
6. Project: Use KEY (e.g., "PA") not name or ID

### ⚠️ CRITICAL: Unbounded Query Error
**THE FIX**: Add time filter to EVERY JQL query:
- ✅ `project = "PA" AND assignee = currentUser() AND resolution IS EMPTY AND updated >= -30d`
- ❌ `project = "PA" AND assignee = currentUser() AND resolution IS EMPTY` (UNBOUNDED!)

**Time ranges**:
- Last week: `updated >= -7d`
- Last month: `updated >= -30d`
- Last 3 months: `updated >= -90d`
- This year: `updated >= startOfYear()`
"""

CONFLUENCE_GUIDANCE = r"""
## Confluence-Specific Guidance

### Tool Selection
- CREATE page → use `confluence.create_page`
- SEARCH/FIND page → use `confluence.search_pages`
- GET/READ pages → use `confluence.get_pages_in_space` or `confluence.get_page_content`

### Critical Parameter Names (Common Mistakes)

**confluence.search_pages:**
- ✅ CORRECT: `{"title": "Page Name"}`
- ❌ WRONG: `{"query": "..."}` or `{"cql": "..."}`

**confluence.create_page:**
- ✅ CORRECT: `{"space_id": "123", "page_title": "...", "page_content": "..."}`
- ❌ WRONG: `{"title": "..."}` (use `page_title` not `title`)
- ❌ WRONG: `{"content": "..."}` (use `page_content` not `content`)

**confluence.get_page_content:**
- ✅ CORRECT: `{"page_id": "12345"}`
- ❌ WRONG: `{"id": "..."}` or `{"pageId": "..."}`

### Space ID Resolution for create_page
1. **Check Reference Data first** - if `type: "confluence_space"` exists, use its `id` field
2. **If not in Reference Data**:
   - If space_id is a key (non-numeric): Call `confluence.get_spaces`, find matching space, extract numeric `id`
   - If space_id is numeric: Use directly
3. **CRITICAL**: API requires numeric space IDs. Always use `id` field, never `key` field.

### Example Cascading Pattern

Search for page, then get its content:
```json
{
  "tools": [
    {"name": "confluence.search_pages", "args": {"title": "My Page"}},
    {"name": "confluence.get_page_content", "args": {"page_id": "{{confluence.search_pages.data.results.id}}"}}
  ]
}
```
"""

PLANNER_SYSTEM_PROMPT = """You are an intelligent task planner for an enterprise AI assistant. Your role is to understand user intent and select the appropriate tools to fulfill their request.

## Core Planning Logic - Understanding User Intent

**Decision Tree (Follow in Order):**
1. **Simple greeting/thanks?** → `can_answer_directly: true`
2. **User references previous discussion?** → Use conversation context, minimal tools
3. **User wants to PERFORM an action?** (create/update/delete/modify) → Use appropriate service tools
4. **User wants LIVE/REAL-TIME data from a connected service?** → Use service tools
5. **User wants INFORMATION ABOUT something?** (from knowledge base/docs) → Use `retrieval.search_internal_knowledge`
6. **Documentation creation with KB citations in history?** → Start with `retrieval.search_internal_knowledge`

## Tool Selection Principles

**Read tool descriptions carefully** - Each tool has a description, parameters, and usage examples. Use these to determine if a tool matches the user's intent.

**Use SERVICE TOOLS when:**
- User wants **LIVE/REAL-TIME data** from a connected service (e.g., "list items", "show records", "get data from X")
- User wants to **PERFORM an action** (create/update/delete/modify resources)
- User wants **current status** of items in a service
- User explicitly asks for data **from** a specific service
- Tool description matches the user's request

**Use RETRIEVAL when:**
- User wants **INFORMATION ABOUT** a topic/person/concept (e.g., "what is X", "tell me about Y", "who is Z")
- User wants **DOCUMENTATION** or **KNOWLEDGE** (e.g., "how to X", "best practices for Y")
- User asks **GENERAL QUESTIONS** that could be answered from knowledge base
- Query is **AMBIGUOUS** and could be answered from indexed knowledge
- No service tool description matches the request

**Key Distinction:**
- **LIVE data requests:** "list/get/show/fetch [items] from [service]" → Use service tools
- **Information requests:** "what/explain/tell me about [topic]" → Use retrieval
- **Action requests:** "create/update/delete [resource]" → Use service tools

**Important:** Service data might also be indexed in the knowledge base. Choose based on intent:
- User wants current/live data → Service tools
- User wants information/explanation → Retrieval

## Available Tools
{available_tools}

**How to Use Tool Descriptions:**
- Each tool has a name, description, parameters, and usage examples
- Read the tool description to understand what it does
- Check parameter schemas to see required vs optional fields
- Match user intent to tool purpose, not just keywords
- If multiple tools could work, choose the one that best matches the user's intent
- Tool descriptions are your primary guide for tool selection

## Cascading Tools (Multi-Step Tasks)

**When one tool's output feeds into another, use placeholders:**

Format: `{{{{tool_name.data.field}}}}`

**CRITICAL: NEVER pass instruction text as parameter values**
- ❌ WRONG: `{{"space_id": "Use the numeric id from get_spaces results"}}`
- ❌ WRONG: `{{"space_id": "Resolve the numeric id for space name/key from results"}}`
- ✅ CORRECT: `{{"space_id": "{{{{confluence.get_spaces.data.results[0].id}}}}"}}`

**Example:**
```json
{{
  "tools": [
    {{"name": "confluence.get_spaces", "args": {{}}}},
    {{"name": "confluence.get_pages_in_space", "args": {{"space_id": "{{{{confluence.get_spaces.data.results[0].id}}}}"}}}}
  ]
}}
```

**Placeholder rules:**
- Simple: `{{{{tool_name.field}}}}`
- Nested: `{{{{tool_name.data.nested.field}}}}`
- Arrays: `{{{{tool_name.data.results[0].id}}}}` (use [0] for first item, [1] for second, etc.)
- Multiple levels: `{{{{tool_name.data.results[0].space.id}}}}`
- Tools execute sequentially when placeholders detected

**How to extract from arrays:**
- If result is `{{"data": {{"results": [{{"id": "123"}}, {{"id": "456"}}]}}}}`
- Use `{{{{tool_name.data.results[0].id}}}}` to get "123"
- Use `{{{{tool_name.data.results[1].id}}}}` to get "456"

**Finding the right field path:**
1. Look at the tool's return description
2. Check the tool result structure
3. Use dot notation to navigate: `data.results[0].id`
4. Use array index [0] for first item in arrays

**Common patterns:**
- Get first result: `{{{{tool.data.results[0].field}}}}`
- Get nested field: `{{{{tool.data.item.nested_field}}}}`
- Get by index: `{{{{tool.data.items[2].id}}}}`

## Context Reuse (CRITICAL)

**Before planning, check conversation history:**
- Was this content already discussed? → Use it directly
- Did user say "this/that/above"? → Refers to previous message
- Is user adding/modifying previous data? → Don't re-fetch

**Example:**
```
Previous: Assistant showed resource details from a service
Current: User says "add this to another service"
Action: Use conversation context, call ONLY the action tool needed
DON'T: Re-fetch data that was already displayed
```

**General rule:** Conversation context beats tool calls.

## Documentation Creation & Content Synthesis

**When user asks to create documentation:**
- If previous messages have KB citations → Start with `retrieval.search_internal_knowledge`
- If creating from scratch → Use `retrieval.search_internal_knowledge` with topic keywords

**Pattern:**
```json
{{
  "tools": [
    {{"name": "retrieval.search_internal_knowledge", "args": {{"query": "topic keywords"}}}},
    {{"name": "service.create_document", "args": {{
      "title": "...",
      "content": "Content will be synthesized by respond_node from retrieval results"
    }}}}
  ]
}}
```

**Key principle:** Planner fetches data, respond_node synthesizes final content. Don't hardcode long content in planner.

{jira_guidance}
{confluence_guidance}

## Planning Best Practices

**Retrieval:**
- Max 2-3 calls per request
- Queries under 50 chars
- Broad keywords only

**Error handling:**
- First fail: Fix and retry
- Second fail: Ask user
- Permission error: Inform immediately

**Clarification (ONLY for Actions):**
Set `needs_clarification: true` ONLY if:
- User wants to PERFORM an action (create/update/delete/modify)
- AND a required parameter is missing (check tool schema for required fields)
- AND you cannot infer it from conversation context or reference data

**DO NOT ask for clarification if:**
- User wants INFORMATION (what/who/how questions) → Use retrieval - it will search and find relevant content
- User wants LIVE data but query is ambiguous → Try service tools with reasonable defaults, or use retrieval if service tools fail
- Query mentions a name/topic → Use retrieval to find it
- User asks "tell me about X" or "what is X" → Use retrieval
- Optional parameters are missing → Use tool defaults or omit them

## Reference Data & User Context

**Check Reference Data first:**
- Previous responses may have needed IDs/keys
- Use directly instead of calling tools

**User asking about themselves:**
- Use provided user info directly
- Set `can_answer_directly: true`

## Output (JSON only)
{{
  "intent": "Brief description",
  "reasoning": "Why these tools",
  "can_answer_directly": false,
  "needs_clarification": false,
  "clarifying_question": "",
  "tools": [
    {{"name": "tool.name", "args": {{"param": "value"}}}}
  ]
}}

**Return ONLY valid JSON, no markdown.**"""


# ============================================================================
# JIRA GUIDANCE - CONDENSED
# ============================================================================

JIRA_GUIDANCE = r"""
## Jira Rules

**Never fabricate:**
- ❌ Don't invent accountIds/emails
- ✅ Use `jira.search_users(query="user email")`

**JQL syntax:**
- Unresolved: `resolution IS EMPTY`
- Current user: `assignee = currentUser()`
- **ALWAYS add time filter:** `AND updated >= -30d`
- Quote text: `status = "Open"`

**Common time filters:**
- Week: `updated >= -7d`
- Month: `updated >= -30d`
- Quarter: `updated >= -90d`
"""


# ============================================================================
# CONFLUENCE GUIDANCE - CONDENSED
# ============================================================================

CONFLUENCE_GUIDANCE = r"""
## Confluence Rules

**Tool selection:**
- Create → `confluence.create_page`
- Update → `confluence.update_page`
- Search → `confluence.search_pages`
- Read → `confluence.get_page_content`

**Critical parameters (check tool schema):**
- `search_pages`: Uses `title` parameter (not `query`)
- `create_page`: Uses `page_title`, `page_content`, `space_id`
- `update_page`: Uses `page_id`, `page_title` (optional), `page_content` (optional)
- `get_page_content`: Uses `page_id`

**Space ID resolution:**
1. Check Reference Data for `id`
2. If not found: Call `confluence.get_spaces`, extract numeric `id`
3. Use numeric ID (not key/name)

## CRITICAL: Content Synthesis for Confluence Pages

### ❌ DON'T: Hardcode Long Content in Planner
```json
{{
  "tools": [{{
    "name": "confluence.update_page",
    "args": {{
      "page_id": "123",
      "page_content": "<h1>Title</h1><p>Paragraph 1...</p><p>Paragraph 2...</p>..."
    }}
  }}]
}}
```

### ✅ DO: Let respond_node Synthesize Content

**Pattern 1: Use Placeholders (for simple data)**
```json
{{
  "tools": [{{
    "name": "confluence.get_page_content",
    "args": {{"page_id": "456"}}
  }}, {{
    "name": "confluence.update_page",
    "args": {{
      "page_id": "123",
      "page_title": "{{{{confluence.get_page_content.data.title}}}}",
      "page_content": "Content will be synthesized by respond_node from fetched page"
    }}
  }}]
}}
```

**Pattern 2: Brief Description (for complex synthesis)**
```json
{{
  "tools": [{{
    "name": "retrieval.search_internal_knowledge",
    "args": {{"query": "deployment guide"}}
  }}, {{
    "name": "confluence.get_page_content",
    "args": {{"page_id": "page1"}}
  }}, {{
    "name": "confluence.update_page",
    "args": {{
      "page_id": "summary_page",
      "page_content": "Combined summary of pages - respond_node will synthesize from tool results"
    }}
  }}]
}}
```

**How it works:**
1. Planner fetches the data (get_page_content, retrieval, etc.)
2. Planner calls update_page with brief placeholder text
3. **respond_node** sees all tool results and synthesizes the actual HTML content
4. respond_node updates the page with complete, formatted content

**Key insight:** The `page_content` in planner args is just a **signal** to respond_node. The actual content synthesis happens in respond_node using ALL tool results.

## Example: Update Page with Summary of Multiple Pages

**User request:** "Get pages 123 and 456, summarize them, update page 789"

**Correct plan:**
```json
{{
  "tools": [
    {{"name": "confluence.get_page_content", "args": {{"page_id": "123"}}}},
    {{"name": "confluence.get_page_content", "args": {{"page_id": "456"}}}},
    {{"name": "confluence.update_page", "args": {{
      "page_id": "789",
      "page_title": "Combined Summary",
      "page_content": "Summary of pages 123 and 456 - respond_node will generate from fetched content"
    }}}}
  ]
}}
```

**What respond_node does:**
1. Sees tool results for pages 123 and 456 with full content
2. Synthesizes a comprehensive summary in HTML
3. Updates page 789 with the synthesized content

**Don't:**
- ❌ Try to put full HTML in page_content
- ❌ Use placeholders for long content (they'll fail)
- ❌ Truncate content to fit token limits

**Do:**
- ✅ Fetch all needed data with tools
- ✅ Use brief description in page_content
- ✅ Let respond_node synthesize the final HTML
"""

PLANNER_USER_TEMPLATE = """Query: {query}

Plan the tools. Return only valid JSON."""

PLANNER_USER_TEMPLATE_WITH_CONTEXT = """## Conversation History
{conversation_history}

## Current Query
{query}

Plan the tools using conversation context. Return only valid JSON."""


# ============================================================================
# REFLECTION PROMPT - IMPROVED DECISION MAKING
# ============================================================================

REFLECT_PROMPT = """Analyze tool execution results and decide next action.

## Execution Results
{execution_summary}

## User Query
{query}

## Status
- Retry: {retry_count}/{max_retries}
- Iteration: {iteration_count}/{max_iterations}

## Decision Options

1. **respond_success** - Task completed successfully
   - Use when: Tools succeeded AND task is complete
   - Example: User asked to "get tickets", tickets retrieved

2. **respond_error** - Unrecoverable error
   - Use when: Permissions issue, resource not found, rate limit
   - Example: 403 Forbidden, 404 Not Found

3. **respond_clarify** - Need user input
   - Use when: Ambiguous query, missing critical info
   - Example: Unbounded JQL after retry

4. **retry_with_fix** - Fixable error, retry possible
   - Use when: Syntax error, type error, correctable mistake
   - Example: Wrong parameter type, invalid JQL syntax

5. **continue_with_more_tools** - Need more steps
   - Use when: Tools succeeded but task incomplete
   - Example: User asked to "create and comment", only created

## Task Completion Check

**Complete** if:
- User asked to "get/list" AND we got data → respond_success
- User asked to "create" AND we created → respond_success
- All requested actions done → respond_success

**Incomplete** if:
- User asked to "create and comment" but only created → continue_with_more_tools
- User asked to "update" but only retrieved data → continue_with_more_tools
- Task has multiple parts and not all done → continue_with_more_tools

## Common Error Fixes
- "Unbounded JQL" → Add `AND updated >= -30d`
- "User not found" → Call `jira.search_users` first
- "Invalid type" → Check parameter types, convert if needed
- "Space ID type error" → Call `confluence.get_spaces` to get numeric ID

## Handling Empty/Null Results

### When Search Returns Empty

**Pattern**: `{{"results": []}}` or `{{"data": []}}`

**Decision Logic:**
1. Check if content was in conversation history → respond_success with conversation data
2. Check if task was "search" → respond_success (found nothing is valid result)
3. Check if task needs content → respond_clarify (ask for correct name/location)

**Example:**
- Search for "Page X" → empty results
- BUT user just discussed "Page X" in previous message
- → respond_success and use conversation content

### Empty Result Recovery
```json
{{
  "decision": "respond_success",
  "reasoning": "Search returned empty but content exists in conversation history",
  "task_complete": true
}}
```

**When to use conversation context:**
- Search returned empty results
- BUT previous assistant message contains the information user needs
- User is referencing content that was just displayed
- → respond_success and let respond_node extract from conversation

**When to clarify:**
- Search returned empty results
- No conversation history with relevant content
- User provided specific name/location that doesn't exist
- → respond_clarify to ask for correct information

## Output (JSON only)
{{
  "decision": "respond_success|respond_error|respond_clarify|retry_with_fix|continue_with_more_tools",
  "reasoning": "Brief explanation",
  "fix_instruction": "For retry: what to change",
  "clarifying_question": "For clarify: what to ask",
  "error_context": "For error: user-friendly explanation",
  "task_complete": true/false,
  "needs_more_tools": "What tools needed next (if continue)"
}}"""


# ============================================================================
# PLANNER NODE - IMPROVED ACCURACY
# ============================================================================

async def planner_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """
    LLM-driven planner with improved accuracy and error handling.

    Features:
    - Smart tool validation with retry
    - Better prompt construction
    - Cascading tool support
    - Context-aware planning
    """
    start_time = time.perf_counter()
    log = state.get("logger", logger)
    llm = state.get("llm")
    query = state.get("query", "")

    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "planning", "message": "Planning..."}
    }, config)

    # Build system prompt with tool descriptions
    tool_descriptions = _get_cached_tool_descriptions(state, log)
    jira_guidance = JIRA_GUIDANCE if _has_jira_tools(state) else ""
    confluence_guidance = CONFLUENCE_GUIDANCE if _has_confluence_tools(state) else ""

    system_prompt = PLANNER_SYSTEM_PROMPT.format(
        available_tools=tool_descriptions,
        jira_guidance=jira_guidance,
        confluence_guidance=confluence_guidance
    )

    # Build user prompt with context
    user_prompt = _build_planner_user_prompt(state, query, log)

    # Add retry/continue context if needed
    if state.get("is_retry"):
        user_prompt = _build_retry_context(state) + "\n\n" + user_prompt
        state["is_retry"] = False
        log.info("🔄 Retry mode active")

    if state.get("is_continue"):
        user_prompt = _build_continue_context(state, log) + "\n\n" + user_prompt
        state["is_continue"] = False
        log.info("➡️ Continue mode active")

    # Plan with validation retry loop
    plan = await _plan_with_validation_retry(
        llm, system_prompt, user_prompt, state, log, query
    )

    # Store plan in state
    state["execution_plan"] = plan
    state["planned_tool_calls"] = plan.get("tools", [])
    state["pending_tool_calls"] = bool(plan.get("tools"))
    state["query_analysis"] = {
        "intent": plan.get("intent", ""),
        "reasoning": plan.get("reasoning", ""),
        "can_answer_directly": plan.get("can_answer_directly", False),
    }

    # Handle clarification request
    if plan.get("needs_clarification"):
        state["reflection_decision"] = "respond_clarify"
        state["reflection"] = {
            "decision": "respond_clarify",
            "reasoning": "Planner needs clarification",
            "clarifying_question": plan.get("clarifying_question", "Could you provide more details?")
        }
        log.info(f"❓ Requesting clarification: {plan.get('clarifying_question', '')[:50]}...")

    duration_ms = (time.perf_counter() - start_time) * 1000
    log.info(f"⚡ Planner: {duration_ms:.0f}ms - {len(plan.get('tools', []))} tools")

    return state


# ============================================================================
# PLANNER HELPER FUNCTIONS
# ============================================================================

def _detect_kb_citations_in_history(conversations: List[Dict]) -> bool:
    """Detect if recent responses have KB citations"""
    for conv in conversations[-3:]:  # Check last 3 messages
        if conv.get("role") == "bot_response":
            content = conv.get("content", "")
            # Check for KB citation patterns
            if any(pattern in content for pattern in ["[R", "KB\n", "PipesHub-Readme"]):
                return True
    return False


def _extract_topic_from_kb_context(conversations: List[Dict]) -> str:
    """Extract main topic from KB-cited responses"""
    for conv in reversed(conversations[-3:]):
        if conv.get("role") == "bot_response":
            content = conv.get("content", "")
            if "[R" in content or "KB" in content:
                # Extract first heading or key topic
                lines = content.split("\n")
                for line in lines:
                    if line.startswith("#"):
                        return line.replace("#", "").strip()
    return ""




def _detect_content_reference(query: str, conversations: List[Dict], log: logging.Logger) -> bool:
    """
    Detect if user is referencing content from previous messages.

    Triggers ONLY when:
    - User says "add this/that to..." after detailed content was shown
    - User says "use the above" after content was displayed

    DO NOT trigger for:
    - "create a page of the above" → This needs NEW page creation, not reuse
    - "update page with summary of X" → Needs fetching X, not reusing prev content
    """
    query_lower = query.lower()

    # ✅ Strong reuse signals (definitely using prev content)
    strong_reuse = [
        "add this to",
        "add that to",
        "send this to",
        "post this to",
        "use the above for"
    ]

    # ❌ Weak signals (might need new data)
    weak_signals = [
        "create",
        "make",
        "generate",  # Creating something NEW
        "update with summary",  # Needs fetching + synthesis
        "page of the above",  # Misleading - needs creation
    ]

    # Check for strong reuse signals
    has_strong_reuse = any(signal in query_lower for signal in strong_reuse)
    if not has_strong_reuse:
        return False

    # Check for weak signals that override
    has_weak_signal = any(signal in query_lower for signal in weak_signals)
    if has_weak_signal:
        log.debug("Weak signal detected - not treating as content reuse")
        return False

    # Check if previous message had detailed content
    if not conversations:
        return False

    last_bot_message = None
    for conv in reversed(conversations):
        if conv.get("role") == "bot_response":
            last_bot_message = conv
            break

    if not last_bot_message:
        return False

    content = last_bot_message.get("content", "")

    # Heuristic: If last response was long and detailed (>500 chars), likely contains reusable content
    if len(content) > 500:
        log.debug(f"Previous response has {len(content)} chars - treating as reusable content")
        return True

    return False


def _build_planner_user_prompt(state: ChatState, query: str, log: logging.Logger) -> str:
    """Build user prompt with conversation context and smart content detection"""
    previous_conversations = state.get("previous_conversations", [])

    # Detect KB content in history
    has_kb_citations = _detect_kb_citations_in_history(previous_conversations)

    # Detect if user is referencing previously discussed content
    is_referencing_previous = _detect_content_reference(query, previous_conversations, log)

    # Build base prompt
    if previous_conversations:
        conversation_history = _format_conversation_history(previous_conversations, log)
        user_prompt = PLANNER_USER_TEMPLATE_WITH_CONTEXT.format(
            conversation_history=conversation_history,
            query=query
        )
        log.debug(f"Using {len(previous_conversations)} previous messages")

        # Add context reuse hint if user references previous content
        if is_referencing_previous:
            context_hint = """
## 🎯 CONTENT REUSE DETECTED
User is referencing content from previous messages.
Previous assistant messages contain the information user needs.
**DON'T re-fetch what was already shown.**
**USE conversation context directly** - respond_node will extract and format.
"""
            user_prompt = context_hint + "\n\n" + user_prompt
            log.info("📌 Content reference detected - using conversation context")

        # Add retrieval hint if KB citations detected (but not if already using conversation context)
        elif has_kb_citations and any(word in query.lower() for word in ["create", "page", "document", "above", "summary", "this", "that"]):
            topic = _extract_topic_from_kb_context(previous_conversations)
            retrieval_hint = f"""
## 🔍 RETRIEVAL REQUIRED DETECTED
Previous response had KB citations about: {topic}
User wants to create documentation → **USE retrieval.search_internal_knowledge FIRST**
Query suggestion: "{topic.lower()}"
"""
            user_prompt = retrieval_hint + "\n\n" + user_prompt
            log.info(f"📚 KB content detected - adding retrieval hint for topic: {topic}")
    else:
        user_prompt = PLANNER_USER_TEMPLATE.format(query=query)

    # Add user context
    user_context = _format_user_context(state)
    if user_context:
        user_prompt = user_prompt + "\n\n" + user_context

    return user_prompt


def _format_user_context(state: ChatState) -> str:
    """Format user information for planner"""
    user_info = state.get("user_info", {})
    org_info = state.get("org_info", {})

    user_email = state.get("user_email") or user_info.get("userEmail") or user_info.get("email") or ""
    user_name = (
        user_info.get("fullName") or
        user_info.get("name") or
        user_info.get("displayName") or
        (f"{user_info.get('firstName', '')} {user_info.get('lastName', '')}".strip()
         if user_info.get("firstName") or user_info.get("lastName") else "")
    )

    if not user_email and not user_name:
        return ""

    parts = ["## Current User Information", ""]

    if user_name:
        parts.append(f"- **Name**: {user_name}")
    if user_email:
        parts.append(f"- **Email**: {user_email}")

    if org_info.get("accountType"):
        parts.append(f"- **Account Type**: {org_info['accountType']}")

    if user_email or user_name:
        parts.append("")
        parts.append("### Usage:")
        parts.append("")

        if _has_jira_tools(state):
            parts.append("**Jira (current user):**")
            parts.append("- ✅ Use `currentUser()` in JQL: `assignee = currentUser()`")
            parts.append("- ❌ DON'T call `jira.search_users` for yourself")
            parts.append("")

        parts.append("**General:**")
        parts.append("- **When user asks about themselves**: use this info DIRECTLY with `can_answer_directly: true`")
        parts.append("")

    return "\n".join(parts)


def _format_conversation_history(conversations: List[Dict], log: logging.Logger) -> str:
    """Format conversation history with reference data"""
    if not conversations:
        return ""

    recent = conversations[-5:]
    lines = []
    all_reference_data = []

    for conv in recent:
        role = conv.get("role", "")
        content = conv.get("content", "")

        if role == "user_query":
            content = content[:USER_QUERY_MAX_LENGTH] if len(content) > USER_QUERY_MAX_LENGTH else content
            lines.append(f"User: {content}")
        elif role == "bot_response":
            content = content[:BOT_RESPONSE_MAX_LENGTH] if len(content) > BOT_RESPONSE_MAX_LENGTH else content
            lines.append(f"Assistant: {content}")

            ref_data = conv.get("referenceData", [])
            if ref_data:
                all_reference_data.extend(ref_data)

    result = "\n".join(lines)

    # Add reference data if available
    if all_reference_data:
        result += "\n\n## Reference Data (from previous responses - use these IDs/keys directly):\n"

        # Group by type
        spaces = [item for item in all_reference_data if item.get("type") == "confluence_space"]
        projects = [item for item in all_reference_data if item.get("type") == "jira_project"]
        issues = [item for item in all_reference_data if item.get("type") == "jira_issue"]

        if spaces:
            result += "**Confluence Spaces** (use `id` for space_id): "
            result += ", ".join([f"{item.get('name', '?')} (id={item.get('id', '?')})" for item in spaces[:5]])
            result += "\n"

        if projects:
            result += "**Jira Projects** (use `key`): "
            result += ", ".join([f"{item.get('name', '?')} (key={item.get('key', '?')})" for item in projects[:5]])
            result += "\n"

        if issues:
            result += "**Jira Issues** (use `key`): "
            result += ", ".join([f"{item.get('key', '?')}" for item in issues[:5]])
            result += "\n"

        log.debug(f"📎 Included {len(all_reference_data)} reference items")

    return result


def _extract_missing_params_from_error(error_msg: str) -> List[str]:
    """Extract missing parameter names from validation error"""
    missing = []

    # Pattern: "page_title\n  Field required"
    pattern = r'(\w+)\s+Field required'
    matches = re.findall(pattern, error_msg, re.IGNORECASE)
    missing.extend(matches)

    # Pattern: "Field required [type=missing, input_value={...}, input_type=dict]"
    # Extract field name from context
    pattern2 = r'(\w+)\s*\n\s*Field required'
    matches2 = re.findall(pattern2, error_msg, re.IGNORECASE | re.MULTILINE)
    missing.extend(matches2)

    return list(set(missing))  # Remove duplicates


def _extract_invalid_params_from_args(args: Dict, error_msg: str) -> List[str]:
    """Detect parameters that were provided but not expected"""
    # This is harder - would need to compare against schema
    # For now, just return empty
    return []


def _build_retry_context(state: ChatState) -> str:
    """Build context for retry with error details"""
    errors = state.get("execution_errors", [])
    reflection = state.get("reflection", {})
    fix_instruction = reflection.get("fix_instruction", "")

    if not errors:
        return ""

    error_summary = errors[0]
    failed_tool = error_summary.get('tool_name', 'unknown')
    failed_args = error_summary.get("args", {})
    error_msg = error_summary.get('error', 'unknown')[:500]

    # Extract missing/invalid parameters from error
    missing_params = _extract_missing_params_from_error(error_msg)
    invalid_params = _extract_invalid_params_from_args(failed_args, error_msg)

    retry_context = f"""## 🔴 RETRY MODE - PREVIOUS ATTEMPT FAILED

**Failed Tool**: {failed_tool}
**Error**: {error_msg}

**Previous Args**:
```json
{json.dumps(failed_args, indent=2)}
```

**Fix Instruction**: {fix_instruction}
"""

    # Add parameter hints if validation error
    if "validation error" in error_msg.lower() or "field required" in error_msg.lower():
        retry_context += "\n## ⚠️ PARAMETER VALIDATION ERROR\n\n"

        if missing_params:
            retry_context += f"**Missing required parameters**: {', '.join(missing_params)}\n"

        if invalid_params:
            retry_context += f"**Invalid parameters used**: {', '.join(invalid_params)}\n"

        retry_context += "\n**CHECK TOOL SCHEMA**: Look at the parameter list for this tool above.\n"
        retry_context += "**USE EXACT PARAMETER NAMES** from the schema.\n\n"

    retry_context += """
**IMPORTANT**:
- If user asked to CREATE, you MUST still use CREATE tool after fixing
- Fix the parameters and retry with corrected values
- Don't switch to different tool type
- Use EXACT parameter names from tool schema
"""

    return retry_context


def _build_continue_context(state: ChatState, log: logging.Logger) -> str:
    """Build context for continuing with more tools"""
    tool_results = state.get("all_tool_results", [])
    if not tool_results:
        return ""

    # Format last 5 results
    result_parts = []
    for result in tool_results[-5:]:
        tool_name = result.get("tool_name", "unknown")
        status = result.get("status", "unknown")
        result_data = str(result.get("result", ""))[:500]

        result_parts.append(f"- {tool_name} ({status}): {result_data}")

    return f"""## 📋 PREVIOUS TOOL RESULTS (use this data for next steps)

{chr(10).join(result_parts)}

**IMPORTANT**: Use the data above to plan next steps. Extract IDs, keys, and other values.
"""


async def _plan_with_validation_retry(
    llm: Any,
    system_prompt: str,
    user_prompt: str,
    state: ChatState,
    log: logging.Logger,
    query: str
) -> Dict[str, Any]:
    """
    Plan with tool validation retry loop.

    If planner suggests invalid tools, retry with error message showing available tools.
    """
    validation_retry_count = state.get("tool_validation_retry_count", 0)
    max_retries = NodeConfig.MAX_VALIDATION_RETRIES

    invoke_config = {"callbacks": [_opik_tracer]} if _opik_tracer else {}

    while validation_retry_count <= max_retries:
        try:
            # Call LLM
            response = await asyncio.wait_for(
                llm.ainvoke(
                    [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)],
                    config=invoke_config
                ),
                timeout=NodeConfig.PLANNER_TIMEOUT_SECONDS
            )

            # Parse response
            plan = _parse_planner_response(
                response.content if hasattr(response, 'content') else str(response),
                log
            )

            # Validate tools
            tools = plan.get('tools', [])
            is_valid, invalid_tools, available_tool_names = _validate_planned_tools(tools, state, log)

            if is_valid or validation_retry_count >= max_retries:
                # Success or max retries reached
                if not is_valid:
                    log.error(f"⚠️ Invalid tools after {max_retries} retries: {invalid_tools}. Removing them.")
                    plan["tools"] = [t for t in tools if isinstance(t, dict) and t.get('name', '') not in invalid_tools]

                state["tool_validation_retry_count"] = 0
                return plan
            else:
                # Retry with error message
                validation_retry_count += 1
                state["tool_validation_retry_count"] = validation_retry_count
                log.warning(f"⚠️ Invalid tools: {invalid_tools}. Retry {validation_retry_count}/{max_retries}")

                # Build error message
                available_list = ", ".join(sorted(available_tool_names)[:MAX_AVAILABLE_TOOLS_DISPLAY])
                if len(available_tool_names) > MAX_AVAILABLE_TOOLS_DISPLAY:
                    available_list += f" (and {len(available_tool_names) - MAX_AVAILABLE_TOOLS_DISPLAY} more)"

                error_message = f"""❌ ERROR: Invalid tools: {', '.join(invalid_tools)}

**Available tools**: {available_list}

Choose tools ONLY from the available list above.

**Original query**: {query}
"""
                user_prompt = error_message + "\n\n" + user_prompt

        except asyncio.TimeoutError:
            log.warning("⏱️ Planner timeout")
            return _create_fallback_plan(query)
        except Exception as e:
            log.error(f"💥 Planner error: {e}")
            return _create_fallback_plan(query)

    # Should never reach here
    return _create_fallback_plan(query)


def _parse_planner_response(content: str, log: logging.Logger) -> Dict[str, Any]:
    """Parse planner JSON response with error handling"""
    content = content.strip()

    # Remove markdown code blocks
    if "```json" in content:
        match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
        if match:
            content = match.group(1)
    elif content.startswith("```"):
        content = re.sub(r'^```\s*\n?', '', content)
        content = re.sub(r'\n?```\s*$', '', content)

    try:
        plan = json.loads(content)

        if isinstance(plan, dict):
            # Set defaults
            plan.setdefault("intent", "")
            plan.setdefault("reasoning", "")
            plan.setdefault("can_answer_directly", False)
            plan.setdefault("needs_clarification", False)
            plan.setdefault("clarifying_question", "")
            plan.setdefault("tools", [])

            # Normalize tools
            normalized_tools = []
            for tool in plan.get("tools", []):
                if isinstance(tool, dict) and "name" in tool:
                    normalized_tools.append({
                        "name": tool["name"],
                        "args": tool.get("args", {})
                    })

            # Limit retrieval queries
            retrieval_tools = [t for t in normalized_tools if "retrieval" in t.get("name", "").lower()]
            if len(retrieval_tools) > NodeConfig.MAX_RETRIEVAL_QUERIES:
                log.warning(f"Too many retrieval queries ({len(retrieval_tools)}), limiting to {NodeConfig.MAX_RETRIEVAL_QUERIES}")
                other_tools = [t for t in normalized_tools if "retrieval" not in t.get("name", "").lower()]
                normalized_tools = retrieval_tools[:NodeConfig.MAX_RETRIEVAL_QUERIES] + other_tools

            # Trim overly long queries
            for tool in normalized_tools:
                if "retrieval" in tool.get("name", "").lower():
                    query = tool.get("args", {}).get("query", "")
                    if len(query) > NodeConfig.MAX_QUERY_LENGTH:
                        words = query.split()[:NodeConfig.MAX_QUERY_WORDS]
                        trimmed = " ".join(words)
                        log.warning(f"Trimmed query: '{query[:50]}...' → '{trimmed}'")
                        tool["args"]["query"] = trimmed

            plan["tools"] = normalized_tools
            return plan

    except json.JSONDecodeError as e:
        log.warning(f"Failed to parse planner response: {e}")

    return _create_fallback_plan("")


def _create_fallback_plan(query: str) -> Dict[str, Any]:
    """Create fallback plan when parsing fails"""
    return {
        "intent": "Fallback: Search internal knowledge",
        "reasoning": "Planner failed, using fallback",
        "can_answer_directly": False,
        "needs_clarification": False,
        "clarifying_question": "",
        "tools": [{"name": "retrieval.search_internal_knowledge", "args": {"query": query}}]
    }


def _validate_planned_tools(
    planned_tools: List[Dict[str, Any]],
    state: ChatState,
    log: logging.Logger
) -> Tuple[bool, List[str], List[str]]:
    """
    Validate planned tool names against available tools.

    Returns:
        (is_valid, invalid_tools, available_tool_names)
    """
    try:
        from app.modules.agents.qna.tool_system import (
            _sanitize_tool_name_if_needed,
            get_agent_tools_with_schemas,
        )

        tools = get_agent_tools_with_schemas(state)
        llm = state.get("llm")

        # Get available tool names
        available_tool_names = {getattr(tool, 'name', str(tool)) for tool in tools}

        # Check for invalid tools
        invalid_tools = []
        for tool_call in planned_tools:
            if isinstance(tool_call, dict):
                tool_name = tool_call.get('name', '')
                normalized_name = _sanitize_tool_name_if_needed(tool_name, llm) if llm else tool_name

                if normalized_name not in available_tool_names and tool_name not in available_tool_names:
                    invalid_tools.append(tool_name)

        is_valid = len(invalid_tools) == 0
        return is_valid, invalid_tools, list(available_tool_names)

    except Exception as e:
        log.warning(f"Tool validation failed: {e}")
        return True, [], []


def _has_jira_tools(state: ChatState) -> bool:
    """Check if Jira tools available"""
    agent_toolsets = state.get("agent_toolsets", [])
    return any(isinstance(ts, dict) and "jira" in ts.get("name", "").lower() for ts in agent_toolsets)


def _has_confluence_tools(state: ChatState) -> bool:
    """Check if Confluence tools available"""
    agent_toolsets = state.get("agent_toolsets", [])
    return any(isinstance(ts, dict) and "confluence" in ts.get("name", "").lower() for ts in agent_toolsets)


# Tool description caching
_tool_description_cache: Dict[str, str] = {}


def _get_cached_tool_descriptions(state: ChatState, log: logging.Logger) -> str:
    """Get tool descriptions with caching"""
    org_id = state.get("org_id", "default")
    agent_toolsets = state.get("agent_toolsets", [])
    llm = state.get("llm")

    from app.modules.agents.qna.tool_system import (
        _requires_sanitized_tool_names,
        get_agent_tools_with_schemas,
    )

    llm_type = "anthropic" if llm and _requires_sanitized_tool_names(llm) else "other"
    toolset_names = sorted([ts.get("name", "") for ts in agent_toolsets if isinstance(ts, dict)])
    cache_key = f"{org_id}_{hash(tuple(toolset_names))}_{llm_type}"

    if cache_key in _tool_description_cache:
        return _tool_description_cache[cache_key]

    try:
        tools = get_agent_tools_with_schemas(state)
        if not tools:
            fallback_name = "retrieval_search_internal_knowledge" if llm_type == "anthropic" else "retrieval.search_internal_knowledge"
            return f"### {fallback_name}\n  ✅ Use: Questions about company info, policies\n  ❌ Don't: External API calls"

        result = _format_tool_descriptions(tools, log)
        _tool_description_cache[cache_key] = result
        return result

    except Exception as e:
        log.warning(f"Tool load failed: {e}")
        return "### retrieval.search_internal_knowledge\n  ✅ Use: Search company knowledge"


def _get_field_type_name(field_info) -> str:
    """Get type name from Pydantic v2 field"""
    try:
        annotation = field_info.annotation

        # Handle Optional types
        if hasattr(annotation, '__origin__'):
            origin = annotation.__origin__
            if origin is Union:
                # Get non-None type
                args = [arg for arg in annotation.__args__ if arg is not type(None)]
                if args:
                    annotation = args[0]

        # Get type name
        if hasattr(annotation, '__name__'):
            return annotation.__name__.lower()
        else:
            type_str = str(annotation).lower()
            # Clean up common type representations
            type_str = type_str.replace('<class ', '').replace('>', '').replace("'", "")
            return type_str
    except Exception:
        return "any"


def _get_field_type_name_v1(field_info) -> str:
    """Get type name from Pydantic v1 field"""
    try:
        type_ = field_info.outer_type_

        # Handle Optional
        if hasattr(type_, '__origin__') and type_.__origin__ is Union:
            args = [arg for arg in type_.__args__ if arg is not type(None)]
            if args:
                type_ = args[0]

        if hasattr(type_, '__name__'):
            return type_.__name__.lower()
        else:
            return str(type_).lower()
    except Exception:
        return "any"


def _extract_parameters_from_schema(schema: Any, log: logging.Logger) -> Dict[str, Dict[str, Any]]:
    """
    Extract parameter information from Pydantic schema.

    Returns:
        {
            "param_name": {
                "type": "string",
                "required": True,
                "description": "..."
            }
        }
    """
    try:
        # Handle Pydantic v2 schema
        if hasattr(schema, 'model_fields'):
            fields = schema.model_fields
            required_fields = getattr(schema, '__required_fields__', set())

            params = {}
            for field_name, field_info in fields.items():
                # Check if field is required
                is_required = (
                    field_name in required_fields or
                    (hasattr(field_info, 'is_required') and field_info.is_required()) or
                    (not hasattr(field_info, 'default') or field_info.default is None)
                )

                param_info = {
                    "required": is_required,
                    "description": getattr(field_info, 'description', '') or "",
                    "type": _get_field_type_name(field_info)
                }
                params[field_name] = param_info

            return params

        # Handle Pydantic v1 schema
        elif hasattr(schema, '__fields__'):
            fields = schema.__fields__
            params = {}

            for field_name, field_info in fields.items():
                param_info = {
                    "required": field_info.required,
                    "description": getattr(field_info.field_info, 'description', '') or "",
                    "type": _get_field_type_name_v1(field_info)
                }
                params[field_name] = param_info

            return params

        # Handle dict schema (JSON schema)
        elif isinstance(schema, dict):
            properties = schema.get("properties", {})
            required = schema.get("required", [])

            params = {}
            for param_name, param_schema in properties.items():
                param_info = {
                    "required": param_name in required,
                    "description": param_schema.get("description", ""),
                    "type": param_schema.get("type", "any")
                }
                params[param_name] = param_info

            return params

    except Exception as e:
        log.debug(f"Schema extraction failed: {e}")

    return {}


def _format_tool_descriptions(tools: List, log: logging.Logger) -> str:
    """
    Format tool descriptions for planner with parameter schemas.

    Includes:
    - Tool name
    - Description
    - Required parameters with types
    - Optional parameters (if space allows)
    """
    lines = []

    for tool in tools[:30]:  # Limit to prevent prompt bloat
        name = getattr(tool, 'name', str(tool))
        description = getattr(tool, 'description', '')

        # Start with name and description
        lines.append(f"### {name}")
        if description:
            # Truncate long descriptions
            desc_text = description[:200] if len(description) > 200 else description
            lines.append(f"  {desc_text}")

        # Extract parameter schema
        try:
            schema = getattr(tool, 'args_schema', None)
            if schema:
                params_info = _extract_parameters_from_schema(schema, log)
                if params_info:
                    lines.append("  **Parameters:**")
                    for param_name, param_info in params_info.items():
                        required_marker = "**required**" if param_info.get("required") else "optional"
                        param_type = param_info.get("type", "any").upper()
                        param_desc = param_info.get("description", "")

                        # Format: - param_name (required): description [TYPE]
                        if param_desc:
                            lines.append(f"  - `{param_name}` ({required_marker}): {param_desc[:80]} [{param_type}]")
                        else:
                            lines.append(f"  - `{param_name}` ({required_marker}) [{param_type}]")
        except Exception as e:
            log.debug(f"Could not extract schema for {name}: {e}")

        lines.append("")

    return "\n".join(lines)


# ============================================================================
# PART 3: EXECUTE, REFLECT, RESPOND NODES + COMPLETE SYSTEM
# ============================================================================

# ============================================================================
# EXECUTE NODE - WITH CASCADING SUPPORT
# ============================================================================

async def execute_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """
    Execute planned tools with cascading support.

    Features:
    - Automatic detection of cascading needs
    - Sequential execution with placeholder resolution
    - Parallel execution when no dependencies
    - Comprehensive error handling
    """
    start_time = time.perf_counter()
    log = state.get("logger", logger)

    planned_tools = state.get("planned_tool_calls", [])

    if not planned_tools:
        log.info("No tools to execute")
        state["pending_tool_calls"] = False
        return state

    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "executing", "message": f"Executing {len(planned_tools)} tool(s)..."}
    }, config)

    # Get available tools
    try:
        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

        tools = get_agent_tools_with_schemas(state)
        llm = state.get("llm")

        # Build tool mapping
        tools_by_name = {}
        for t in tools:
            sanitized_name = getattr(t, 'name', str(t))
            tools_by_name[sanitized_name] = t
            original_name = getattr(t, '_original_name', sanitized_name)
            if original_name != sanitized_name:
                tools_by_name[original_name] = t

    except Exception as e:
        log.error(f"Failed to get tools: {e}")
        tools_by_name = {}

    # Execute tools (cascading detection handled internally)
    tool_results = await ToolExecutor.execute_tools(
        planned_tools, tools_by_name, llm, state, log, writer, config
    )

    # Build tool messages
    tool_messages = []
    for result in tool_results:
        if result.get("tool_id"):
            content_str = format_result_for_llm(result.get("result", ""), result.get("tool_name", ""))
            tool_messages.append(ToolMessage(
                content=content_str,
                tool_call_id=result.get("tool_id", "")
            ))

    # Update state
    state["tool_results"] = tool_results
    state["all_tool_results"] = tool_results

    if not state.get("messages"):
        state["messages"] = []
    state["messages"].extend(tool_messages)

    state["pending_tool_calls"] = False

    # Log summary
    success_count = sum(1 for r in tool_results if r.get("status") == "success")
    failed_count = sum(1 for r in tool_results if r.get("status") == "error")

    duration_ms = (time.perf_counter() - start_time) * 1000
    log.info(f"✅ Executed {len(tool_results)} tools in {duration_ms:.0f}ms ({success_count} ✓, {failed_count} ✗)")

    return state


# ============================================================================
# REFLECT NODE - SMART DECISION MAKING
# ============================================================================

async def reflect_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """
    Analyze tool results and decide next action.

    Features:
    - Partial success detection
    - Primary tool success detection
    - Smart error categorization
    - Context-aware retry decisions
    """
    start_time = time.perf_counter()
    log = state.get("logger", logger)

    tool_results = state.get("all_tool_results", [])
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", NodeConfig.MAX_RETRIES)
    iteration_count = state.get("iteration_count", 0)
    max_iterations = state.get("max_iterations", NodeConfig.MAX_ITERATIONS)

    # Count successes and failures
    successful = [r for r in tool_results if r.get("status") == "success"]
    failed = [r for r in tool_results if r.get("status") == "error"]

    log.info(f"📊 Tool results: {len(successful)} ✓, {len(failed)} ✗")

    # Log details for debugging
    for r in successful:
        log.debug(f"  ✅ {r.get('tool_name')}")
    for r in failed:
        log.debug(f"  ❌ {r.get('tool_name')}: {str(r.get('result', ''))[:100]}")

    # ========================================================================
    # DECISION 1: Partial Success (some succeeded, some failed)
    # ========================================================================

    if len(successful) > 0 and len(failed) > 0:
        log.info("🔀 Partial success detected")

        # Check if primary tool succeeded
        query = state.get("query", "").lower()
        primary_succeeded = _check_primary_tool_success(query, successful, log)

        # Check if we have retrieval results
        has_retrieval = any("retrieval" in r.get("tool_name", "").lower() for r in successful)

        if primary_succeeded or has_retrieval:
            log.info("✅ Primary tool or retrieval succeeded - proceeding")
            state["reflection_decision"] = "respond_success"
            state["reflection"] = {
                "decision": "respond_success",
                "reasoning": f"Primary task completed ({len(successful)} succeeded, ignoring {len(failed)} secondary failures)",
                "task_complete": True
            }
            duration_ms = (time.perf_counter() - start_time) * 1000
            log.info(f"⚡ Reflect: respond_success (partial) - {duration_ms:.0f}ms")
            return state

    # ========================================================================
    # DECISION 2: All Succeeded - Check if Task Complete
    # ========================================================================

    if not failed:
        query = state.get("query", "").lower()
        executed_tools = [r.get("tool_name", "") for r in tool_results]

        # Check if task needs more steps
        needs_continue = _check_if_task_needs_continue(query, executed_tools, tool_results, log)

        if needs_continue and iteration_count < max_iterations:
            state["reflection_decision"] = "continue_with_more_tools"
            state["reflection"] = {
                "decision": "continue_with_more_tools",
                "reasoning": "Tools succeeded but task incomplete",
                "task_complete": False
            }
            log.info(f"➡️ Continue needed (iteration {iteration_count + 1}/{max_iterations})")
            duration_ms = (time.perf_counter() - start_time) * 1000
            log.info(f"⚡ Reflect: continue - {duration_ms:.0f}ms")
            return state
        else:
            state["reflection_decision"] = "respond_success"
            state["reflection"] = {
                "decision": "respond_success",
                "reasoning": "All succeeded" if not needs_continue else "Max iterations reached",
                "task_complete": not needs_continue
            }
            duration_ms = (time.perf_counter() - start_time) * 1000
            log.info(f"⚡ Reflect: respond_success (all done) - {duration_ms:.0f}ms")
            return state

    # ========================================================================
    # DECISION 3: Check Primary Tool Success (for cascading)
    # ========================================================================

    planned_tools = state.get("planned_tool_calls", [])
    if planned_tools and len(planned_tools) > 0 and len(successful) > 0:
        primary_tool_name = planned_tools[0].get("name", "").lower()

        # Check if primary (first) tool succeeded
        for result in successful:
            tool_name = result.get("tool_name", "").lower()
            normalized_primary = primary_tool_name.replace('.', '_')
            normalized_tool = tool_name.replace('.', '_')

            if tool_name == primary_tool_name or normalized_tool == normalized_primary:
                log.info(f"✅ Primary action succeeded: {tool_name}")
                state["reflection_decision"] = "respond_success"
                state["reflection"] = {
                    "decision": "respond_success",
                    "reasoning": "Primary action succeeded (dependent tools failed but task complete)",
                    "task_complete": True
                }
                duration_ms = (time.perf_counter() - start_time) * 1000
                log.info(f"⚡ Reflect: respond_success (primary) - {duration_ms:.0f}ms")
                return state

    # ========================================================================
    # DECISION 4: Fast Path Error Detection
    # ========================================================================

    error_text = " ".join(str(r.get("result", "")) for r in failed).lower()

    # Unrecoverable errors
    unrecoverable = [
        "permission", "unauthorized", "forbidden", "403",
        "not found", "does not exist", "404",
        "authentication", "auth failed", "invalid token",
        "rate limit", "quota exceeded"
    ]

    if any(pattern in error_text for pattern in unrecoverable):
        error_context = "Permission or access issue"
        if "not found" in error_text or "does not exist" in error_text:
            error_context = "Resource not found"
        elif "rate limit" in error_text or "quota" in error_text:
            error_context = "Rate limit reached"

        state["reflection_decision"] = "respond_error"
        state["reflection"] = {
            "decision": "respond_error",
            "reasoning": "Unrecoverable error",
            "error_context": error_context
        }
        log.info(f"❌ Unrecoverable error: {error_context}")
        duration_ms = (time.perf_counter() - start_time) * 1000
        log.info(f"⚡ Reflect: respond_error - {duration_ms:.0f}ms")
        return state

    # ========================================================================
    # DECISION 5: Recoverable Errors (Retry Logic)
    # ========================================================================

    if retry_count < max_retries:
        # Unbounded JQL
        if "unbounded" in error_text:
            state["reflection_decision"] = "retry_with_fix"
            state["reflection"] = {
                "decision": "retry_with_fix",
                "reasoning": "Unbounded JQL",
                "fix_instruction": "Add time filter: `AND updated >= -30d`"
            }
            log.info("🔄 Retry: Unbounded JQL")
            duration_ms = (time.perf_counter() - start_time) * 1000
            log.info(f"⚡ Reflect: retry_with_fix - {duration_ms:.0f}ms")
            return state

        # Type errors
        if "not the correct type" in error_text or "expected type" in error_text:
            state["reflection_decision"] = "retry_with_fix"
            state["reflection"] = {
                "decision": "retry_with_fix",
                "reasoning": "Parameter type error",
                "fix_instruction": "Check parameter types and convert to correct format (e.g., numeric ID instead of string key)"
            }
            log.info("🔄 Retry: Type error")
            duration_ms = (time.perf_counter() - start_time) * 1000
            log.info(f"⚡ Reflect: retry_with_fix - {duration_ms:.0f}ms")
            return state

        # Syntax errors
        if any(x in error_text for x in ["syntax", "invalid", "malformed", "parse error"]):
            state["reflection_decision"] = "retry_with_fix"
            state["reflection"] = {
                "decision": "retry_with_fix",
                "reasoning": "Syntax error",
                "fix_instruction": "Fix query syntax based on error message"
            }
            log.info("🔄 Retry: Syntax error")
            duration_ms = (time.perf_counter() - start_time) * 1000
            log.info(f"⚡ Reflect: retry_with_fix - {duration_ms:.0f}ms")
            return state

    # ========================================================================
    # DECISION 6: LLM-Based Reflection (Complex Cases)
    # ========================================================================

    llm = state.get("llm")

    # Build summary
    summary_parts = []
    for r in tool_results:
        status = "SUCCESS" if r.get("status") == "success" else "FAILED"
        tool_name = r.get("tool_name", "unknown")
        result_str = str(r.get("result", ""))[:300]
        summary_parts.append(f"[{status}] {tool_name}: {result_str}")

    prompt = REFLECT_PROMPT.format(
        execution_summary="\n".join(summary_parts),
        query=state.get("query", ""),
        retry_count=retry_count,
        max_retries=max_retries,
        iteration_count=iteration_count,
        max_iterations=max_iterations
    )

    try:
        safe_stream_write(writer, {
            "event": "status",
            "data": {"status": "analyzing", "message": "Analyzing results..."}
        }, config)

        response = await asyncio.wait_for(
            llm.ainvoke([
                SystemMessage(content=prompt),
                HumanMessage(content="Analyze and decide.")
            ]),
            timeout=NodeConfig.REFLECTION_TIMEOUT_SECONDS
        )

        reflection = _parse_reflection_response(response.content, log)

    except asyncio.TimeoutError:
        log.warning("⏱️ Reflect timeout")
        reflection = {
            "decision": "respond_error",
            "reasoning": "Analysis timeout",
            "error_context": "Unable to complete request"
        }
    except Exception as e:
        log.error(f"💥 Reflection failed: {e}")
        reflection = {
            "decision": "respond_error",
            "reasoning": str(e),
            "error_context": "Error processing request"
        }

    state["reflection"] = reflection
    state["reflection_decision"] = reflection.get("decision", "respond_error")

    duration_ms = (time.perf_counter() - start_time) * 1000
    log.info(f"⚡ Reflect: {state['reflection_decision']} (LLM) - {duration_ms:.0f}ms")

    return state


def _parse_reflection_response(content: str, log: logging.Logger) -> Dict[str, Any]:
    """Parse reflection JSON response"""
    content = content.strip()

    # Remove markdown
    if "```json" in content:
        match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
        if match:
            content = match.group(1)
    elif content.startswith("```"):
        content = re.sub(r'^```\s*\n?', '', content)
        content = re.sub(r'\n?```\s*$', '', content)

    try:
        reflection = json.loads(content)

        if isinstance(reflection, dict):
            reflection.setdefault("decision", "respond_error")
            reflection.setdefault("reasoning", "")
            reflection.setdefault("fix_instruction", "")
            reflection.setdefault("clarifying_question", "")
            reflection.setdefault("error_context", "")
            reflection.setdefault("task_complete", True)
            return reflection

    except json.JSONDecodeError as e:
        log.warning(f"Failed to parse reflection: {e}")

    return {
        "decision": "respond_error",
        "reasoning": "Parse failed",
        "error_context": "Unable to process request"
    }


def _check_primary_tool_success(query: str, successful: List[Dict], log: logging.Logger) -> bool:
    """
    Check if primary tool succeeded based on user intent.

    Examples:
    - "Create ticket and comment" → primary is create
    - "Search and update" → primary is search
    """
    query_lower = query.lower()
    successful_tools = [r.get("tool_name", "").lower() for r in successful]

    # Map intent to tool patterns
    intent_patterns = {
        "create": ["create", "make", "add", "new"],
        "update": ["update", "modify", "change", "edit"],
        "delete": ["delete", "remove", "clear"],
        "search": ["search", "find", "get", "list"],
    }

    # Detect primary intent
    primary_intent = None
    for intent, keywords in intent_patterns.items():
        if any(keyword in query_lower for keyword in keywords):
            primary_intent = intent
            break

    if not primary_intent:
        return len(successful) > 0  # Fallback: any success

    # Check if primary intent tool succeeded
    for tool in successful_tools:
        if primary_intent in tool:
            log.debug(f"✅ Primary '{primary_intent}' tool succeeded: {tool}")
            return True

    return len(successful) > 0  # Fallback


def _check_if_task_needs_continue(
    query: str,
    executed_tools: List[str],
    tool_results: List[Dict[str, Any]],
    log: logging.Logger
) -> bool:
    """
    Check if task needs more steps.

    Returns True if task is incomplete.
    """
    query_lower = query.lower()
    executed_lower = [t.lower() for t in executed_tools]

    # User wants to edit/update but only got data
    if any(word in query_lower for word in ["edit", "update", "modify", "change"]):
        has_read = any(x in t for x in ["get", "read", "fetch", "search", "find"] for t in executed_lower)
        has_write = any(x in t for x in ["update", "edit", "modify", "send"] for t in executed_lower)

        if has_read and not has_write:
            log.debug("Task incomplete: wants to update but only read")
            return True

    # User wants to create but only searched
    if any(word in query_lower for word in ["create", "make", "new", "add"]):
        has_search = any(x in t for x in ["search", "find", "get", "fetch"] for t in executed_lower)
        has_create = any(x in t for x in ["create", "make", "add", "post", "send"] for t in executed_lower)

        if has_search and not has_create:
            log.debug("Task incomplete: wants to create but only searched")
            return True

    return False


# ============================================================================
# PREPARE RETRY NODE
# ============================================================================

async def prepare_retry_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """Prepare for retry after fixable error"""
    log = state.get("logger", logger)

    state["retry_count"] = state.get("retry_count", 0) + 1
    state["is_retry"] = True

    # Extract errors
    tool_results = state.get("all_tool_results", [])
    errors = []
    for r in tool_results:
        if r.get("status") == "error":
            errors.append({
                "tool_name": r.get("tool_name", "unknown"),
                "args": r.get("args", {}),
                "error": str(r.get("result", ""))[:300]
            })

    state["execution_errors"] = errors

    # Clear old results
    state["all_tool_results"] = []
    state["tool_results"] = []

    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "retrying", "message": "Retrying..."}
    }, config)

    log.info(f"🔄 Retry {state['retry_count']}/{state.get('max_retries', NodeConfig.MAX_RETRIES)}: {len(errors)} errors")

    return state


# ============================================================================
# PREPARE CONTINUE NODE
# ============================================================================

async def prepare_continue_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """Prepare to continue with more tools"""
    log = state.get("logger", logger)

    state["iteration_count"] = state.get("iteration_count", 0) + 1
    state["is_continue"] = True

    # Keep tool results for next planning

    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "continuing", "message": "Continuing..."}
    }, config)

    max_iterations = state.get("max_iterations", NodeConfig.MAX_ITERATIONS)
    log.info(f"➡️ Continue {state['iteration_count']}/{max_iterations}")

    return state


# ============================================================================
# RESPOND NODE - FINAL RESPONSE GENERATION
# ============================================================================

async def respond_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """
    Generate final response with streaming.

    Features:
    - Streaming response generation
    - Citation extraction for retrieval results
    - Reference data extraction for API results
    - Proper error handling
    """
    start_time = time.perf_counter()
    log = state.get("logger", logger)
    llm = state.get("llm")

    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "generating", "message": "Generating response..."}
    }, config)

    # Handle error state
    if state.get("error"):
        error = state["error"]
        error_msg = error.get("message", error.get("detail", "An error occurred"))
        error_response = {
            "answer": error_msg,
            "citations": [],
            "confidence": "Low",
            "answerMatchType": "Error"
        }
        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {"chunk": error_msg, "accumulated": error_msg, "citations": []}
        }, config)
        safe_stream_write(writer, {"event": "complete", "data": error_response}, config)
        state["response"] = error_msg
        state["completion_data"] = error_response
        return state

    # Check if direct answer
    execution_plan = state.get("execution_plan", {})
    tool_results = state.get("all_tool_results", [])

    if execution_plan.get("can_answer_directly") and not tool_results:
        response = await _generate_direct_response(state, llm, log, writer, config)
        completion = {
            "answer": response,
            "citations": [],
            "confidence": "High",
            "answerMatchType": "Direct Response"
        }
        safe_stream_write(writer, {"event": "complete", "data": completion}, config)
        state["response"] = response
        state["completion_data"] = completion
        return state

    # Handle clarification
    reflection_decision = state.get("reflection_decision", "respond_success")
    reflection = state.get("reflection", {})

    if reflection_decision == "respond_clarify":
        clarifying_question = reflection.get("clarifying_question", "Could you provide more details?")
        clarify_response = {
            "answer": clarifying_question,
            "citations": [],
            "confidence": "Medium",
            "answerMatchType": "Clarification Needed"
        }
        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {"chunk": clarifying_question, "accumulated": clarifying_question, "citations": []}
        }, config)
        safe_stream_write(writer, {"event": "complete", "data": clarify_response}, config)
        state["response"] = clarifying_question
        state["completion_data"] = clarify_response
        return state

    # Handle errors
    successful_count = sum(1 for r in tool_results if r.get("status") == "success")

    if reflection_decision == "respond_error" and successful_count == 0:
        error_context = reflection.get("error_context", "")

        # Build error message
        failed_errors = []
        for r in tool_results:
            if r.get("status") == "error":
                tool_name = r.get("tool_name", "unknown")
                error_result = r.get("result", "Unknown error")
                error_str = str(error_result)[:150]
                failed_errors.append(f"{tool_name}: {error_str}")

        if error_context:
            error_msg = f"I wasn't able to complete that request. {error_context}\n\nPlease try again."
        elif failed_errors:
            error_details = "\n".join(failed_errors[:2])
            error_msg = f"I encountered an error:\n{error_details}\n\nPlease check settings or try again."
        else:
            error_msg = "I wasn't able to complete that request. Please try again."

        error_response = {
            "answer": error_msg,
            "citations": [],
            "confidence": "Low",
            "answerMatchType": "Tool Execution Failed"
        }

        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {"chunk": error_msg, "accumulated": error_msg, "citations": []}
        }, config)
        safe_stream_write(writer, {"event": "complete", "data": error_response}, config)

        state["response"] = error_msg
        state["completion_data"] = error_response
        return state

    # Generate success response
    final_results = state.get("final_results", [])
    virtual_record_map = state.get("virtual_record_id_to_result", {})
    tool_records = state.get("tool_records", [])

    log.info(f"📚 Citation data: {len(final_results)} results, {len(virtual_record_map)} records")

    # Build messages
    messages = create_response_messages(state)

    if tool_results or final_results:
        context = _build_tool_results_context(tool_results, final_results)
        if context.strip():
            if messages and isinstance(messages[-1], HumanMessage):
                messages[-1].content += context
            else:
                messages.append(HumanMessage(content=context))

    try:
        log.info("🎯 Using stream_llm_response...")

        answer_text = ""
        citations = []
        reason = None
        confidence = None
        reference_data = []

        async for stream_event in stream_llm_response(
            llm=llm,
            messages=messages,
            final_results=final_results,
            logger=log,
            target_words_per_chunk=1,
            mode="json",
            virtual_record_id_to_result=virtual_record_map,
            records=tool_records,
        ):
            event_type = stream_event.get("event")
            event_data = stream_event.get("data", {})

            safe_stream_write(writer, {"event": event_type, "data": event_data}, config)

            if event_type == "complete":
                answer_text = event_data.get("answer", "")
                citations = event_data.get("citations", [])
                reason = event_data.get("reason")
                confidence = event_data.get("confidence")
                reference_data = event_data.get("referenceData", [])

        if not answer_text or len(answer_text.strip()) == 0:
            log.warning("⚠️ Empty response, using fallback")
            answer_text = "I wasn't able to generate a response. Please try rephrasing."

            fallback_response = {
                "answer": answer_text,
                "citations": [],
                "confidence": "Low",
                "answerMatchType": "Fallback Response"
            }

            safe_stream_write(writer, {
                "event": "answer_chunk",
                "data": {"chunk": answer_text, "accumulated": answer_text, "citations": []}
            }, config)
            safe_stream_write(writer, {"event": "complete", "data": fallback_response}, config)

            state["response"] = answer_text
            state["completion_data"] = fallback_response
        else:
            completion_data = {
                "answer": answer_text,
                "citations": citations,
                "reason": reason,
                "confidence": confidence,
            }
            if reference_data:
                completion_data["referenceData"] = reference_data
                log.debug(f"📎 Stored {len(reference_data)} reference items")

            state["response"] = answer_text
            state["completion_data"] = completion_data

        log.info(f"✅ Generated response: {len(answer_text)} chars, {len(citations)} citations")

    except Exception as e:
        log.error(f"💥 Response generation failed: {e}", exc_info=True)
        error_msg = "I encountered an issue. Please try again."
        error_response = {
            "answer": error_msg,
            "citations": [],
            "confidence": "Low",
            "answerMatchType": "Error"
        }
        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {"chunk": error_msg, "accumulated": error_msg, "citations": []}
        }, config)
        safe_stream_write(writer, {"event": "complete", "data": error_response}, config)
        state["response"] = error_msg
        state["completion_data"] = error_response

    duration_ms = (time.perf_counter() - start_time) * 1000
    log.info(f"⚡ respond_node: {duration_ms:.0f}ms")

    return state


async def _generate_direct_response(
    state: ChatState,
    llm: Any,
    log: logging.Logger,
    writer: StreamWriter,
    config: RunnableConfig
) -> str:
    """Generate direct response with streaming"""
    query = state.get("query", "")
    previous = state.get("previous_conversations", [])

    # Build context
    context_lines = []
    for conv in previous[-3:]:
        role = conv.get("role", "")
        content = conv.get("content", "")[:200]
        if role == "user_query":
            context_lines.append(f"User: {content}")
        elif role == "bot_response":
            context_lines.append(f"Assistant: {content}...")

    context = "\n".join(context_lines) if context_lines else ""
    user_context = _format_user_context(state)
    user_info_section = f"\n\n{user_context}" if user_context else ""

    system_content = "You are a helpful, friendly AI assistant. Respond naturally and concisely."
    if user_context:
        system_content += "\n\nIMPORTANT: When user asks about themselves, use provided info DIRECTLY."

    user_content = query
    if context:
        user_content = f"{context}\n\nUser: {query}"
    if user_context:
        user_content += user_info_section

    messages = [
        SystemMessage(content=system_content),
        HumanMessage(content=user_content)
    ]

    try:
        full_content = ""

        if hasattr(llm, 'astream'):
            async for chunk in llm.astream(messages):
                if not chunk:
                    continue

                chunk_text = ""
                if hasattr(chunk, 'content'):
                    content = chunk.content
                    if isinstance(content, str):
                        chunk_text = content
                    elif isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                chunk_text += part.get("text", "")
                            elif isinstance(part, str):
                                chunk_text += part

                if chunk_text:
                    full_content += chunk_text
                    safe_stream_write(writer, {
                        "event": "answer_chunk",
                        "data": {"chunk": chunk_text, "accumulated": full_content, "citations": []}
                    }, config)
        else:
            response = await llm.ainvoke(messages)
            full_content = response.content if hasattr(response, 'content') else str(response)
            safe_stream_write(writer, {
                "event": "answer_chunk",
                "data": {"chunk": full_content, "accumulated": full_content, "citations": []}
            }, config)

        return full_content

    except Exception as e:
        log.error(f"💥 Direct response failed: {e}")
        fallback = "I'm here to help! How can I assist you today?"
        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {"chunk": fallback, "accumulated": fallback, "citations": []}
        }, config)
        return fallback


def _build_tool_results_context(tool_results: List[Dict], final_results: List[Dict]) -> str:
    """Build context from tool results for response generation"""
    successful = [r for r in tool_results if r.get("status") == "success"]
    failed = [r for r in tool_results if r.get("status") == "error"]
    has_retrieval = bool(final_results)
    non_retrieval = [r for r in successful if "retrieval" not in r.get("tool_name", "").lower()]

    parts = []

    # All failed
    if failed and not successful:
        parts.append("\n## ⚠️ Tools Failed\n")
        for r in failed[:3]:
            err = str(r.get("result", "Unknown error"))[:200]
            parts.append(f"- {r.get('tool_name', 'unknown')}: {err}\n")
        parts.append("\n❌ DO NOT fabricate data. Explain error to user.\n")
        return "".join(parts)

    # Has data
    if has_retrieval:
        parts.append("\n## 📚 Internal Knowledge Available\n\n")
        parts.append(f"You have {len(final_results)} knowledge blocks.\n")
        parts.append("Cite IMMEDIATELY after facts: [R1-1], [R2-3]\n\n")

    if non_retrieval:
        parts.append("\n## 🔧 API Tool Results\n\n")
        parts.append("Transform data into professional markdown.\n")
        parts.append("Store IDs/keys in referenceData.\n\n")

        for r in non_retrieval[:5]:
            tool_name = r.get('tool_name', 'unknown')
            content = ToolResultExtractor.extract_data_from_result(r.get("result", ""))

            if isinstance(content, (dict, list)):
                content_str = json.dumps(content, indent=2, default=str)
            else:
                content_str = str(content)

            parts.append(f"### {tool_name}\n")
            parts.append(f"```json\n{content_str}\n```\n\n")

    parts.append("\n---\n## 📝 RESPONSE INSTRUCTIONS\n\n")

    if has_retrieval and non_retrieval:
        parts.append("**COMBINED RESPONSE**: Use internal knowledge (with citations) + API data (formatted).\n")
    elif has_retrieval:
        parts.append("**INTERNAL KNOWLEDGE**: Use knowledge blocks with inline citations [R1-1].\n")
    else:
        parts.append("**API DATA**: Transform into professional markdown. Show user-facing IDs (keys), hide internal IDs.\n")

    parts.append("\nReturn ONLY JSON: {\"answer\": \"...\", \"confidence\": \"High\", \"referenceData\": [...]}\n")

    return "".join(parts)


# ============================================================================
# ROUTING FUNCTIONS
# ============================================================================

def should_execute_tools(state: ChatState) -> Literal["execute", "respond"]:
    """Route to execute or respond"""
    planned_tools = state.get("planned_tool_calls", [])
    execution_plan = state.get("execution_plan", {})

    if execution_plan.get("needs_clarification"):
        return "respond"

    if not planned_tools or execution_plan.get("can_answer_directly"):
        return "respond"

    return "execute"


def route_after_reflect(state: ChatState) -> Literal["prepare_retry", "prepare_continue", "respond"]:
    """Route based on reflection decision"""
    decision = state.get("reflection_decision", "respond_success")
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", NodeConfig.MAX_RETRIES)
    iteration_count = state.get("iteration_count", 0)
    max_iterations = state.get("max_iterations", NodeConfig.MAX_ITERATIONS)

    if decision == "retry_with_fix" and retry_count < max_retries:
        return "prepare_retry"

    if decision == "continue_with_more_tools" and iteration_count < max_iterations:
        return "prepare_continue"

    return "respond"


def check_for_error(state: ChatState) -> Literal["error", "continue"]:
    """Check for errors"""
    return "error" if state.get("error") else "continue"

# ============================================================================
# Modern ReAct Agent Node (with Cascading Tool Support)
# ============================================================================

def _process_retrieval_output(result: object, state: ChatState, log: logging.Logger) -> str:
    """Process retrieval tool output"""
    try:
        from app.agents.actions.retrieval.retrieval import RetrievalToolOutput

        retrieval_output = None

        if isinstance(result, dict) and "content" in result and "final_results" in result:
            retrieval_output = RetrievalToolOutput(**result)
        elif isinstance(result, str):
            try:
                data = json.loads(result)
                if isinstance(data, dict) and "content" in data and "final_results" in data:
                    retrieval_output = RetrievalToolOutput(**data)
            except (json.JSONDecodeError, TypeError):
                pass

        if retrieval_output:
            state["final_results"] = retrieval_output.final_results
            state["virtual_record_id_to_result"] = retrieval_output.virtual_record_id_to_result

            if retrieval_output.virtual_record_id_to_result:
                state["tool_records"] = list(retrieval_output.virtual_record_id_to_result.values())

            log.info(f"Retrieved {len(retrieval_output.final_results)} knowledge blocks")
            return retrieval_output.content

    except Exception as e:
        log.warning(f"Could not process retrieval output: {e}")

    return str(result)


async def react_agent_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """
    ReAct agent node with cascading tool execution support.

    This node uses LangGraph's create_react_agent which naturally handles
    cascading tool calls - one tool's output can be used as input to the next.

    The ReAct agent automatically:
    - Observes tool results
    - Decides if more tools are needed
    - Uses results as inputs to next tool
    - Repeats until task complete
    """
    start_time = time.perf_counter()
    log = state.get("logger", logger)
    llm = state.get("llm")
    query = state.get("query", "")

    try:
        from langchain.agents import create_agent
        from langchain_core.messages import HumanMessage, ToolMessage

        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

        safe_stream_write(writer, {
            "event": "status",
            "data": {"status": "thinking", "message": "Thinking..."}
        }, config)

        # Get tools with Pydantic schemas
        tools = get_agent_tools_with_schemas(state)
        log.info(f"ReAct agent loaded {len(tools)} tools with schemas")

        # Build system prompt
        system_prompt = _build_react_system_prompt(state, log)

        # Create ReAct agent - automatically handles cascading tool calls
        agent = create_agent(
            model=llm,
            tools=tools,
        )

        # Build messages with system prompt
        from langchain_core.messages import SystemMessage
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=query)
        ]

        # Execute agent - handles cascading automatically
        final_messages = []
        tool_results = []

        async for chunk in agent.astream(
            {"messages": messages},
            config=config
        ):
            # Process chunk for streaming
            chunk_messages = chunk.get("messages", [])
            final_messages = chunk_messages

            # Stream tool calls and responses
            _process_react_chunk(chunk, writer, config, state, log)

            # Extract tool results as we go
            for msg in chunk_messages:
                if isinstance(msg, ToolMessage):
                    tool_name = msg.name if hasattr(msg, 'name') else "unknown"
                    result_content = msg.content

                    # Process retrieval tool results to extract final_results
                    # Retrieval tool returns JSON string with RetrievalToolOutput structure
                    if "retrieval" in tool_name.lower():
                        try:
                            # Parse JSON string to dict
                            if isinstance(result_content, str):
                                parsed = json.loads(result_content)
                                _process_retrieval_output(parsed, state, log)
                            elif isinstance(result_content, dict):
                                _process_retrieval_output(result_content, state, log)
                            else:
                                # Try to convert to string and parse
                                _process_retrieval_output(str(result_content), state, log)
                        except Exception as e:
                            log.warning(f"Failed to process retrieval output: {e}")
                            # Try direct processing as fallback
                            _process_retrieval_output(result_content, state, log)

                    tool_results.append({
                        "tool_name": tool_name,
                        "status": "success",
                        "result": result_content,
                        "tool_call_id": msg.tool_call_id if hasattr(msg, 'tool_call_id') else None
                    })

        # Get retrieval results (internal knowledge) - may have been populated by retrieval tool
        final_results = state.get("final_results", [])
        virtual_record_map = state.get("virtual_record_id_to_result", {})
        tool_records = state.get("tool_records", [])
        has_retrieval = bool(final_results)

        # Separate retrieval tools from API tools
        # retrieval_tools = [r for r in tool_results if "retrieval" in r.get("tool_name", "").lower()]
        api_tools = [r for r in tool_results if "retrieval" not in r.get("tool_name", "").lower()]

        # Extract final response from messages
        response = _extract_final_response(final_messages, log)

        # If we have retrieval results, we need to use stream_llm_response for proper citation handling
        # This matches how the old system (respond_node) handles responses with citations
        # The ReAct agent may have generated a response, but we need to ensure citations are properly formatted
        if final_results or tool_results:
            # Build messages for response generation (similar to respond_node)
            # This ensures proper citation formatting like the old system
            messages = create_response_messages(state)

            # Add tool results context (same as old system)
            if tool_results or final_results:
                context = _build_tool_results_context(tool_results, final_results)
                if context.strip():
                    if messages and isinstance(messages[-1], HumanMessage):
                        messages[-1].content += context
                    else:
                        messages.append(HumanMessage(content=context))

            # Use stream_llm_response for proper citation handling (same as old system)
            # This ensures citations are properly extracted and formatted
            try:
                answer_text = ""
                citations = []
                reason = None
                confidence = None
                reference_data = []
                block_numbers = []

                async for stream_event in stream_llm_response(
                    llm=llm,
                    messages=messages,
                    final_results=final_results,
                    logger=log,
                    target_words_per_chunk=1,
                    mode="json",
                    virtual_record_id_to_result=virtual_record_map,
                    records=tool_records,
                ):
                    event_type = stream_event.get("event")
                    event_data = stream_event.get("data", {})

                    # Stream events to writer
                    safe_stream_write(writer, {"event": event_type, "data": event_data}, config)

                    if event_type == "complete":
                        answer_text = event_data.get("answer", "")
                        citations = event_data.get("citations", [])
                        reason = event_data.get("reason")
                        confidence = event_data.get("confidence")
                        reference_data = event_data.get("referenceData", [])
                        block_numbers = event_data.get("blockNumbers", [])

                # Use the response from stream_llm_response (has proper citations)
                # Fallback to ReAct agent response if stream_llm_response didn't provide one
                if answer_text:
                    response = answer_text
                elif not response:
                    # If ReAct agent didn't generate a response either, use fallback
                    response = "I completed the task, but couldn't generate a response."

                # Determine answerMatchType
                if has_retrieval and api_tools:
                    answer_match_type = "Derived From Blocks"  # Mixed: retrieval + API
                elif has_retrieval:
                    answer_match_type = "Derived From Blocks"  # Only retrieval
                elif api_tools:
                    answer_match_type = "Derived From Tool Execution"  # Only API tools
                else:
                    answer_match_type = "Direct Answer"  # No tools used

                # Build completion data with proper response type (same format as old system)
                completion_data = {
                    "answer": response,
                    "citations": citations,
                    "confidence": confidence or "High",
                    "answerMatchType": answer_match_type
                }

                if block_numbers:
                    completion_data["blockNumbers"] = block_numbers
                if reference_data:
                    completion_data["referenceData"] = reference_data
                if reason:
                    completion_data["reason"] = reason

                state["completion_data"] = completion_data

            except Exception as e:
                log.error(f"Response generation with citations failed: {e}", exc_info=True)
                # Fallback: extract citations from ReAct agent response
                citations, block_numbers, reference_data, answer_match_type = _extract_citations_and_metadata(
                    tool_results, final_results, has_retrieval, api_tools, response, log, virtual_record_map
                )
                state["completion_data"] = {
                    "answer": response,
                    "citations": citations,
                    "blockNumbers": block_numbers,
                    "referenceData": reference_data,
                    "confidence": "High",
                    "answerMatchType": answer_match_type
                }
        else:
            # No tool results or retrieval - use extracted response directly
            citations, block_numbers, reference_data, answer_match_type = _extract_citations_and_metadata(
                tool_results, final_results, has_retrieval, api_tools, response, log, virtual_record_map
            )
            state["completion_data"] = {
                "answer": response,
                "citations": citations,
                "blockNumbers": block_numbers,
                "referenceData": reference_data,
                "confidence": "High",
                "answerMatchType": answer_match_type
            }

        # Update state
        state["response"] = response
        state["tool_results"] = tool_results
        state["all_tool_results"] = tool_results

        duration_ms = (time.perf_counter() - start_time) * 1000
        log.info(f"⚡ ReAct Agent: {duration_ms:.0f}ms, {len(tool_results)} tool calls")

    except ImportError as e:
        log.error(f"ReAct agent dependencies not available: {e}")
        # Fallback to direct response
        state["response"] = "ReAct agent is not available. Please use the standard agent."
        state["completion_data"] = {
            "answer": state["response"],
            "confidence": "Low",
            "answerMatchType": "Error"
        }
    except Exception as e:
        log.error(f"ReAct agent error: {e}", exc_info=True)
        state["response"] = f"I encountered an error: {str(e)}"
        state["completion_data"] = {
            "answer": state["response"],
            "confidence": "Low",
            "answerMatchType": "Error"
        }

    return state


def _build_react_system_prompt(state: ChatState, log: logging.Logger) -> str:
    """Build system prompt for ReAct agent with citation support"""
    base_prompt = """You are an intelligent AI assistant that can use tools to help users.

## Tool Usage Guidelines

1. **Cascading Tool Calls**: You can call multiple tools in sequence. Use results from one tool as inputs to the next.
   - Example: First call `confluence.get_spaces()` to find a space ID, then use that ID in `confluence.create_page()`

2. **Tool Selection**: Choose the right tool based on user intent:
   - "create"/"make"/"new" → CREATE tools
   - "get"/"find"/"search"/"list" → READ/SEARCH tools
   - "update"/"modify"/"change" → UPDATE tools
   - "delete"/"remove" → DELETE tools

3. **Parameter Validation**: All tool parameters are validated by Pydantic schemas. Use exact parameter names and types.

4. **Error Handling**: If a tool fails, analyze the error and try a different approach or ask for clarification.

5. **Task Completion**: Continue calling tools until the user's request is fully satisfied.

6. **Response Format**: When you have tool results, format your response clearly:
   - For API tool results: Transform data into professional markdown
   - For retrieval/internal knowledge: Include inline citations like [R1-1] after facts
   - Store technical IDs in referenceData for follow-up queries
"""

    # Check for retrieval results and API tools
    final_results = state.get("final_results", [])
    has_retrieval = bool(final_results)

    # Add citation instructions if retrieval results exist
    if has_retrieval:
        base_prompt += """
## Citation Rules (CRITICAL)

When you have internal knowledge from retrieval tools:
1. Put citation IMMEDIATELY after each fact: "Revenue grew 29% [R1-1]."
2. One citation per bracket: [R1-1][R2-3] NOT [R1-1, R2-3]
3. Include ALL cited blocks in your response
4. Do NOT put citations at end of paragraph - inline after each fact
"""

    # Add tool-specific guidance
    if _has_jira_tools(state):
        base_prompt += "\n" + JIRA_GUIDANCE

    if _has_confluence_tools(state):
        base_prompt += "\n" + CONFLUENCE_GUIDANCE

    # Add user context
    user_context = _format_user_context(state)
    if user_context:
        base_prompt += "\n\n" + user_context

    return base_prompt


def _process_react_chunk(
    chunk: Dict,
    writer: StreamWriter,
    config: RunnableConfig,
    state: ChatState,
    log: logging.Logger
) -> None:
    """Process ReAct agent chunk for streaming"""
    try:
        messages = chunk.get("messages", [])

        for msg in messages:
            # Stream tool calls
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tool_call in msg.tool_calls:
                    safe_stream_write(writer, {
                        "event": "tool_call",
                        "data": {
                            "tool": tool_call.get("name", "unknown"),
                            "args": tool_call.get("args", {}),
                            "id": tool_call.get("id", "")
                        }
                    }, config)

            # Stream tool results
            if isinstance(msg, ToolMessage):
                safe_stream_write(writer, {
                    "event": "tool_result",
                    "data": {
                        "tool": getattr(msg, 'name', 'unknown'),
                        "result": msg.content[:MAX_TOOL_RESULT_PREVIEW_LENGTH] if len(msg.content) > MAX_TOOL_RESULT_PREVIEW_LENGTH else msg.content,
                        "status": "success"
                    }
                }, config)

            # Stream AI responses
            if isinstance(msg, AIMessage) and msg.content:
                # Stream content in chunks
                content = msg.content
                chunk_size = 10
                for i in range(0, len(content), chunk_size):
                    chunk_text = content[i:i + chunk_size]
                    safe_stream_write(writer, {
                        "event": "content",
                        "data": {"text": chunk_text}
                    }, config)
    except Exception as e:
        log.warning(f"Error processing ReAct chunk: {e}")


def _extract_final_response(messages: List, log: logging.Logger) -> str:
    """Extract final response from agent messages"""
    # Find last AIMessage with content
    for msg in reversed(messages):
        if hasattr(msg, 'content') and msg.content and not hasattr(msg, 'tool_calls'):
            return str(msg.content)

    # Fallback: find any message with content
    for msg in reversed(messages):
        if hasattr(msg, 'content') and msg.content:
            return str(msg.content)

    log.warning("No response found in ReAct agent messages")
    return "I completed the task, but couldn't generate a response."


def _extract_citations_and_metadata(
    tool_results: List[Dict],
    final_results: List[Dict],
    has_retrieval: bool,
    api_tools: List[Dict],
    response: str,
    log: logging.Logger,
    virtual_record_map: Optional[Dict[str, Dict[str, Any]]] = None
) -> tuple[List[Dict], List[str], List[Dict], str]:
    """
    Extract citations, block numbers, reference data, and determine answerMatchType.

    Returns:
        Tuple of (citations, block_numbers, reference_data, answer_match_type)
    """
    citations = []
    block_numbers = []
    reference_data = []

    # Extract block numbers from response (e.g., [R1-1], [R2-3])
    import re
    block_pattern = re.compile(r'\[R(\d+)-(\d+)\]')
    matches = block_pattern.findall(response)
    for match in matches:
        block_num = f"R{match[0]}-{match[1]}"
        if block_num not in block_numbers:
            block_numbers.append(block_num)

    # Extract citations from retrieval results (same as old system)
    if has_retrieval and final_results:
        # Use block_number from results if available (set by retrieval tool)
        for result in final_results:
            block_num = result.get("block_number")
            if not block_num:
                # Fallback: generate block number from virtual_record_id
                virtual_id = result.get("virtual_record_id", "")
                if virtual_id and virtual_record_map:
                    # Try to get record number from virtual_record_map
                    record_data = virtual_record_map.get(virtual_id, {})
                    record_num = record_data.get("record_number", 1)
                    block_index = result.get("block_index", 0)
                    block_num = f"R{record_num}-{block_index}"
                else:
                    # Last resort: use index
                    idx = final_results.index(result) if result in final_results else 0
                    block_num = f"R1-{idx+1}"

            citations.append({
                "source": result.get("source", "internal_knowledge"),
                "type": "retrieval",
                "content": str(result.get("content", ""))[:200],
                "virtual_id": result.get("virtual_record_id", ""),
                "block_id": block_num
            })

            # Add to block_numbers if not already there
            if block_num and block_num not in block_numbers:
                block_numbers.append(block_num)

    # Extract reference data from API tool results
    for tool_result in api_tools:
        if tool_result.get("status") == "success":
            result = tool_result.get("result", "")
            ref_data = _extract_reference_data_from_result(result, tool_result.get("tool_name", ""))
            if ref_data:
                reference_data.extend(ref_data)

    # Determine answerMatchType
    if has_retrieval and api_tools:
        answer_match_type = "Derived From Blocks"  # Mixed: retrieval + API
    elif has_retrieval:
        answer_match_type = "Derived From Blocks"  # Only retrieval
    elif api_tools:
        answer_match_type = "Derived From Tool Execution"  # Only API tools
    else:
        answer_match_type = "Direct Answer"  # No tools used

    return citations, block_numbers, reference_data, answer_match_type


def _extract_reference_data_from_result(result: object, tool_name: str) -> List[Dict]:
    """Extract reference data (IDs, keys) from tool results for follow-up queries"""
    reference_data = []

    try:
        # Parse result if it's a string
        if isinstance(result, str):
            import json
            try:
                result = json.loads(result)
            except (json.JSONDecodeError, ValueError):
                return reference_data

        # Extract from common structures
        if isinstance(result, dict):
            # Jira issues
            if "data" in result and isinstance(result["data"], dict):
                issues = result["data"].get("issues", [])
                if isinstance(issues, list):
                    for issue in issues:
                        if isinstance(issue, dict):
                            issue_id = issue.get("id", "")
                            issue_key = issue.get("key", "")
                            # Only add if we have at least key or id
                            if issue_key or issue_id:
                                ref_data = {
                                    "name": issue.get("summary", ""),
                                    "key": issue_key,
                                    "type": "jira_issue"
                                }
                                # Only add id if it exists and is not empty
                                if issue_id:
                                    ref_data["id"] = issue_id
                                reference_data.append(ref_data)

            # Jira projects
            if "data" in result and isinstance(result["data"], list):
                for project in result["data"]:
                    if isinstance(project, dict):
                        project_id = project.get("id", "")
                        project_key = project.get("key", "")
                        # Only add if we have at least key or id
                        if project_key or project_id:
                            ref_data = {
                                "name": project.get("name", ""),
                                "key": project_key,
                                "type": "jira_project"
                            }
                            # Only add id if it exists and is not empty
                            if project_id:
                                ref_data["id"] = project_id
                            reference_data.append(ref_data)

            # Direct issue/project
            if "key" in result:
                item_id = result.get("id", "")
                item_key = result.get("key", "")
                if item_key or item_id:
                    ref_data = {
                        "name": result.get("summary") or result.get("name", ""),
                        "key": item_key,
                        "type": "jira_issue" if "summary" in result else "jira_project"
                    }
                    # Only add id if it exists and is not empty
                    if item_id:
                        ref_data["id"] = item_id
                    reference_data.append(ref_data)

        elif isinstance(result, list):
            for item in result:
                if isinstance(item, dict) and "key" in item:
                    item_id = item.get("id", "")
                    item_key = item.get("key", "")
                    if item_key or item_id:
                        ref_data = {
                            "name": item.get("summary") or item.get("name", ""),
                            "key": item_key,
                            "type": "jira_issue" if "summary" in item else "jira_project"
                        }
                        # Only add id if it exists and is not empty
                        if item_id:
                            ref_data["id"] = item_id
                        reference_data.append(ref_data)

    except Exception as e:
        logger.debug(f"Error extracting reference data: {e}")

    return reference_data


# ============================================================================
# EXPORTS
# ============================================================================

__all__ = [
    "planner_node",
    "execute_node",
    "respond_node",
    "reflect_node",
    "prepare_retry_node",
    "prepare_continue_node",
    "should_execute_tools",
    "route_after_reflect",
    "check_for_error",
    "NodeConfig",
    "clean_tool_result",
    "format_result_for_llm",
    "ToolResultExtractor",
    "PlaceholderResolver",
    "ToolExecutor",
    "react_agent_node",
]
