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
import contextlib
import json
import logging
import os
import re
import time
from typing import Any, Literal, Union
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import var_child_runnable_config
from langgraph.types import StreamWriter

from app.modules.agents.capability_summary import build_capability_summary
from app.modules.agents.qna.chat_state import ChatState
from app.modules.agents.qna.stream_utils import safe_stream_write, send_keepalive
from app.modules.qna.response_prompt import create_response_messages
from app.utils.streaming import stream_llm_response, stream_llm_response_with_tools

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
# NOTE: Truncation limits are set high to preserve context. Only truncate if absolutely necessary.
USER_QUERY_MAX_LENGTH = 10000  # Increased significantly to preserve full user queries
BOT_RESPONSE_MAX_LENGTH = 20000  # Increased significantly to preserve full bot responses
MAX_TOOL_RESULT_PREVIEW_LENGTH = 500
MAX_AVAILABLE_TOOLS_DISPLAY = 20
MAX_CONVERSATION_HISTORY = 20  # Number of user+bot message pairs to include (sliding window)

# Truncation / display limits
_RAW_DATA_SIZE_LIMIT = 8000
_TOOL_LOG_LIMIT = 5
_PARAM_DESC_TRUNCATE = 60
_REASONING_DISPLAY_LEN = 200

# Orchestration status taxonomy (metadata fields on tool result dicts)
ORCHESTRATION_STATUS_RESOLVED = "resolved"
ORCHESTRATION_STATUS_PARTIAL = "partial_failure"
ORCHESTRATION_STATUS_CASCADE_BROKEN = "cascade_broken"

# Content detection constants
MIN_CONTENT_LENGTH_FOR_REUSE = 500  # Minimum chars for content to be considered reusable
MIN_PLACEHOLDER_PARTS = 2  # Minimum parts in placeholder for fuzzy matching
MAX_TOOL_DESCRIPTION_LENGTH = 200  # Maximum length for tool descriptions in prompts

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
    RETRIEVAL_TIMEOUT_SECONDS: float = 60.0  # Faster timeout for retrieval
    PLANNER_TIMEOUT_SECONDS: float = 45.0
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
    # KEEP pagination fields for pagination awareness:
    # "nextPageToken", "prevPageToken", "pageToken",  # Keep these!
    # "cursor", "offset",  # Keep for pagination context
    # "pagination",  # Keep pagination info
    # "startAt", "maxResults", "total",  # Keep for pagination awareness
    # "isLast",  # Keep for pagination awareness
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
            if key.startswith(("_", "$")):
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
    def extract_success_status(result: dict[str, Any] | str | tuple[bool, Any] | None) -> bool:
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
        if isinstance(result, tuple) and len(result) >= 1 and isinstance(result[0], bool):
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
    def extract_data_from_result(result: dict[str, Any] | str | tuple[bool, Any] | list[Any] | None) -> dict[str, Any] | str | list[Any] | None:
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
                return json.loads(result)
            except (json.JSONDecodeError, TypeError):
                # ✅ NEW: Return the string directly (for retrieval tool)
                # Retrieval returns formatted string, not JSON
                return result

        return result

    @staticmethod
    def extract_field_from_data(data: dict[str, Any] | list[Any] | str | None, field_path: list[str]) -> dict[str, Any] | list[Any] | str | int | float | bool | None:
        """
        Extract a specific field from data using a field path.

        Examples:
        - ["data", "key"] → data.key
        - ["data", "0", "accountId"] → data[0].accountId
        - ["data", "results", "0", "id"] → data.results[0].id (with fallback if results doesn't exist)

        Handles:
        - Nested dicts
        - Arrays with numeric indices
        - Auto-fallback when incorrect paths are used (e.g., .results when it doesn't exist)
        - JSON strings
        """
        current = data
        i = 0

        while i < len(field_path):
            if current is None:
                return None

            field = field_path[i]

            # Handle dict
            if isinstance(current, dict):
                # Check if field exists
                if field in current:
                    current = current.get(field)
                else:
                    # Fallback: if we're looking for "results" but it doesn't exist,
                    # and we have "data" that's a list, skip "results" and use data[index] directly
                    if field == "results" and "data" in current and isinstance(current.get("data"), list):
                        # Skip "results" and check if next field is an index
                        if i + 1 < len(field_path):
                            try:
                                index = int(field_path[i + 1])
                                data_list = current.get("data")
                                if 0 <= index < len(data_list):
                                    current = data_list[index]
                                    i += 2  # Skip both "results" and the index
                                    continue
                            except (ValueError, TypeError):
                                pass
                    # Generic "data" prefix skip: if the LLM used "data" as a wrapper
                    # but the actual API response doesn't have a "data" key (e.g., Google APIs
                    # return top-level keys directly), skip "data" and continue navigating
                    # the current dict with the remaining path.
                    # Special case: if the NEXT field after "data" is a numeric index (e.g.,
                    # {{tool.data[0].id}} instead of {{tool.data.items[0].id}}), find the
                    # first list in the current dict (items, results, records, ...) and index into it.
                    elif field == "data":
                        if i + 1 < len(field_path):
                            try:
                                next_idx = int(field_path[i + 1])
                                # Next field is numeric — LLM wrote data[N] instead of data.items[N]
                                # Search for a list value in the current dict (priority order)
                                list_data = None
                                for list_key in ("items", "results", "records", "messages", "values", "data", "value"):
                                    candidate = current.get(list_key)
                                    if isinstance(candidate, list):
                                        list_data = candidate
                                        break
                                if list_data is None:
                                    # Fall back to any list value in the dict
                                    for v in current.values():
                                        if isinstance(v, list):
                                            list_data = v
                                            break
                                if list_data is not None and 0 <= next_idx < len(list_data):
                                    current = list_data[next_idx]
                                    i += 2  # consume 'data' and the numeric index
                                    continue
                            except (ValueError, TypeError):
                                pass
                        i += 1
                        continue
                    # Bidirectional alias fallback: content ↔ body (common in Confluence/API responses)
                    # Try the alias if the requested field doesn't exist
                    elif field == "content" and "body" in current:
                        current = current.get("body")
                    elif field == "body" and "content" in current:
                        current = current.get("content")
                    else:
                        return None

                # After getting a field from dict, check if result is a list
                # and if the next field is an index
                if isinstance(current, list):
                    # Check if list is empty first
                    if len(current) == 0:
                        # If we're trying to access an index in an empty list, return None
                        if i + 1 < len(field_path):
                            try:
                                next_index = int(field_path[i + 1])
                                # Empty list, can't access index
                                return None
                            except (ValueError, TypeError):
                                pass
                        return None  # Empty list, nothing to extract

                    # If next field is a numeric index, use it
                    if i + 1 < len(field_path):
                        try:
                            next_index = int(field_path[i + 1])
                            if 0 <= next_index < len(current):
                                current = current[next_index]
                                i += 2  # Skip both current field (already processed) and index
                                continue
                            else:
                                # Index out of bounds
                                return None
                        except (ValueError, TypeError):
                            pass
                    # If we're at the end of the path and current is a list, return the list
                    if i + 1 >= len(field_path):
                        return current
                    # Otherwise, auto-extract from first item for next navigation
                    if len(current) > 0:
                        current = current[0]
                    else:
                        return None

            # Handle array with index
            elif isinstance(current, list):
                try:
                    # Try to parse as numeric index; treat wildcards as 0
                    if field in ('?', '*') or not field.lstrip('-').isdigit():
                        raise ValueError("non-numeric index — use first element")
                    index = int(field)
                    if 0 <= index < len(current):
                        current = current[index]
                    else:
                        return None
                except ValueError:
                    # Wildcard or non-numeric index — treat as field lookup on first item
                    # or default to first element if the field is a known wildcard
                    if field in ('?', '*'):
                        # Wildcard: use first element and continue navigating
                        if len(current) > 0:
                            current = current[0]
                        else:
                            return None
                    elif len(current) > 0 and isinstance(current[0], dict):
                        # Try direct field access on first item
                        if field in current[0]:
                            current = current[0].get(field)
                        # Bidirectional alias fallback: content ↔ body
                        elif field == "content" and "body" in current[0]:
                            current = current[0].get("body")
                        elif field == "body" and "content" in current[0]:
                            current = current[0].get("content")
                        else:
                            return None
                    else:
                        return None

            # Handle JSON string
            elif isinstance(current, str):
                try:
                    parsed = json.loads(current)
                    if isinstance(parsed, dict):
                        # Try direct field access first
                        if field in parsed:
                            current = parsed.get(field)
                        # Bidirectional alias fallback: content ↔ body
                        elif field == "content" and "body" in parsed:
                            current = parsed.get("body")
                        elif field == "body" and "content" in parsed:
                            current = parsed.get("content")
                        else:
                            return None
                    else:
                        return None
                except (json.JSONDecodeError, TypeError):
                    return None
            else:
                return None

            i += 1

        # Post-processing: If we extracted a Confluence body/content object with storage format,
        # automatically extract the value if we're at the end of the path
        # This handles cases like: data.body → automatically extract body.storage.value
        # or data.content → automatically extract content.storage.value
        if isinstance(current, dict):
            # Handle Confluence storage format: {"storage": {"value": "..."}}
            if "storage" in current:
                storage = current.get("storage", {})
                if isinstance(storage, dict) and "value" in storage:
                    # This is a Confluence storage format object, return the actual content
                    return storage.get("value")
            # Handle direct value fields (some APIs return content/body as {"value": "..."})
            # Check if it's a simple dict with just a "value" key
            elif "value" in current and len(current) == 1:
                return current.get("value")

        # If current is already a string (direct content), return it as-is
        if isinstance(current, str):
            return current

        # If current is None here and the last requested field was "id",
        # check whether the parent dict has a usable "key" field instead.
        # This handles Confluence spaces where the API returns id=null but key is set.
        # The _resolve_space_id method will convert the key to a numeric ID at call time.
        if current is None and field_path and field_path[-1] == "id":
            # Re-navigate to the parent to check for a "key" fallback
            try:
                parent = data
                for f in field_path[:-1]:
                    if isinstance(parent, dict):
                        parent = parent.get(f)
                    elif isinstance(parent, list):
                        try:
                            parent = parent[int(f)]
                        except (ValueError, IndexError):
                            parent = None
                            break
                if isinstance(parent, dict) and parent.get("key"):
                    return parent.get("key")
            except Exception:
                pass

        return current


def _is_semantically_empty(result: object) -> bool:
    """Check if a tool result succeeded but contains no meaningful data.

    Used for cascading chain analysis — an empty source tool means
    downstream tools will receive no useful input.
    """
    if result is None:
        return True

    data = ToolResultExtractor.extract_data_from_result(result)
    if data is None:
        return True

    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, dict):
            for key in ("results", "items", "values"):
                lst = inner.get(key)
                if isinstance(lst, list) and len(lst) == 0:
                    return True
        elif isinstance(inner, list) and len(inner) == 0:
            return True
        for key in ("results", "items", "values", "records"):
            if key in data and isinstance(data[key], list) and len(data[key]) == 0:
                return True

    if isinstance(data, list) and len(data) == 0:
        return True

    return False


def _underscore_to_dotted(name: str) -> str:
    """Convert a sanitized tool name back to its most likely dotted form.

    'jira_search_users' → 'jira.search_users'
    'confluence_get_page_content' → 'confluence.get_page_content'
    'knowledgehub_list_files' → 'knowledgehub.list_files'

    Tool names follow 'app.tool_name' format.  The first underscore
    that corresponds to the app/tool separator is replaced with a dot.
    
    If the name already contains a dot, return it as-is (don't create invalid names).
    """
    # If name already has a dot, don't convert (avoid creating invalid names like calculator.calculate.single_operand)
    if '.' in name:
        return name

    parts = name.split('_')
    if len(parts) >= 2:
        # First underscore is the app.tool separator
        return parts[0] + '.' + '_'.join(parts[1:])
    return name


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
    def has_placeholders(cls, args: dict[str, Any]) -> bool:
        """Check if args contain any placeholders"""
        args_str = json.dumps(args, default=str)
        return bool(cls.PLACEHOLDER_PATTERN.search(args_str))

    @classmethod
    def strip_unresolved(
        cls,
        args: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str]]:
        """
        Replace any remaining unresolved {{...}} placeholders with None.

        This allows tool calls to proceed when only *optional* fields have
        unresolved placeholders.  Required fields that are None will be caught
        by Pydantic validation in _validate_and_normalize_args.

        Returns:
            (cleaned_args, list_of_placeholder_names_that_were_stripped)
        """
        stripped: list[str] = []

        def clean_value(value: object) -> object:
            if isinstance(value, str):
                matches = cls.PLACEHOLDER_PATTERN.findall(value)
                if not matches:
                    return value
                # If the whole value is exactly one placeholder, replace with None
                if value.strip() == f"{{{{{matches[0]}}}}}":
                    stripped.extend(matches)
                    return None
                # Partial placeholder inside a longer string – remove the token
                result = value
                for match in matches:
                    stripped.append(match)
                    result = result.replace(f"{{{{{match}}}}}", "")
                return result.strip() or None
            elif isinstance(value, dict):
                return {k: clean_value(v) for k, v in value.items()}
            elif isinstance(value, list):
                return [clean_value(item) for item in value]
            return value

        cleaned = {k: clean_value(v) for k, v in args.items()}
        return cleaned, stripped

    @classmethod
    def resolve_all(
        cls,
        args: dict[str, Any],
        results_by_tool: dict[str, Any],
        log: logging.Logger
    ) -> dict[str, Any]:
        """
        Resolve all placeholders in args using results from previous tools.

        Returns:
            New dict with all placeholders resolved
        """
        resolved = {}

        for key, value in args.items():
            if isinstance(value, str) and '{{' in value:
                matches = cls.PLACEHOLDER_PATTERN.findall(value)
                # If the entire value is exactly one placeholder, preserve the native
                # type of the resolved data (list, dict, int, etc.) instead of
                # coercing it to str via str(replacement).
                if len(matches) == 1 and value.strip() == f"{{{{{matches[0]}}}}}":
                    raw = cls._resolve_single_placeholder(matches[0], results_by_tool, log)
                    resolved[key] = raw if raw is not None else value
                else:
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
        results_by_tool: dict[str, Any],
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
        results_by_tool: dict[str, Any],
        log: logging.Logger
    ) -> dict[str, Any] | list[Any] | str | int | float | bool | None:
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

        # ✅ SPECIAL CASE: Retrieval tool returns a plain-text string (not JSON).
        # No matter what field path the LLM tried to access (e.g. .data.results[0].title),
        # we always return the full retrieved text.  This prevents None-substitution which
        # would strip the field from the downstream tool call and cause the LLM to
        # hallucinate content instead of using the actually-retrieved knowledge.
        if "retrieval" in tool_name.lower() and isinstance(tool_data, str):
            log.info(
                f"✅ Resolved {{{{{placeholder}}}}} → [full retrieval text, {len(tool_data)} chars]"
                + (f" (field path '{field_path}' ignored — retrieval is plain text)" if field_path and field_path != ['data'] else "")
            )
            return tool_data

        # Extract data using field path (for structured results)
        extracted = ToolResultExtractor.extract_field_from_data(tool_data, field_path)

        if extracted is not None:
            log.info(f"✅ Resolved {{{{{placeholder}}}}} → {str(extracted)[:50]}...")
        else:
            log.warning(f"❌ Could not extract field from placeholder: {{{{{placeholder}}}}}")
            log.warning(f"  Field path used: {field_path}")
            # If the LLM used a JSONPath predicate, call it out explicitly
            if '[?' in placeholder or '[*]' in placeholder:
                log.warning(
                    "  ⚠️ Placeholder contained a JSONPath filter/wildcard expression "
                    "(e.g. [?(@.key=='value')]) — normalised to [0]. "
                    "The LLM must use simple numeric indices like [0] in placeholders."
                )
            log.debug(f"Available data: {str(tool_data)[:200]}")
            # Try to show the structure for debugging
            if isinstance(tool_data, dict):
                log.debug(f"Top-level keys: {list(tool_data.keys())}")
                if "data" in tool_data and isinstance(tool_data["data"], dict):
                    log.debug(f"Data keys: {list(tool_data['data'].keys())}")
                    if "results" in tool_data["data"]:
                        results = tool_data["data"]["results"]
                        if isinstance(results, list):
                            if len(results) == 0:
                                log.warning("⚠️ Search returned empty results - cannot access index [0]")
                            elif len(results) > 0:
                                log.debug(f"First result keys: {list(results[0].keys()) if isinstance(results[0], dict) else 'not a dict'}")

        return extracted

    @classmethod
    def _parse_placeholder(
        cls,
        placeholder: str,
        results_by_tool: dict[str, Any]
    ) -> tuple[str | None, list[str]]:
        """
        Parse placeholder into tool_name and field_path.

        Examples:
        - "jira.create_issue.data.key" → ("jira.create_issue", ["data", "key"])
        - "jira.search_users.data[0].accountId" → ("jira.search_users", ["data", "0", "accountId"])
        - "jira.search_users.data.results[0].accountId" → ("jira.search_users", ["data", "0", "accountId"]) (removes .results)
        - "create_issue.key" → ("jira.create_issue", ["key"]) if fuzzy match

        Returns:
            (tool_name, field_path) or (None, []) if can't parse
        """
        # Helper function to parse field path with array indices
        def parse_field_path(path_str: str) -> list[str]:
            """Parse field path handling array indices like [0], [1], [?], [*].

            Non-numeric indices ([?], [*], etc.) are normalised to '0' so that
            LLM-generated placeholders like results[?].id still resolve to the
            first element instead of failing entirely.
            """
            if not path_str:
                return []

            # Split by '.' but preserve array indices
            parts = []
            current = ""
            i = 0
            while i < len(path_str):
                if path_str[i] == '.':
                    if current:
                        parts.append(current)
                        current = ""
                elif path_str[i] == '[':
                    # Found array index
                    if current:
                        parts.append(current)
                        current = ""
                    # Extract index content
                    i += 1
                    index = ""
                    while i < len(path_str) and path_str[i] != ']':
                        index += path_str[i]
                        i += 1
                    if index:
                        # Normalise non-numeric content to first element (index 0).
                        # Covers: [?], [*], [?(@.key=='value')], [?(@.id==123)], any predicate.
                        stripped = index.strip()
                        if stripped and stripped.lstrip('-').isdigit():
                            parts.append(stripped)
                        else:
                            # Any non-numeric token (wildcard / JSONPath predicate) → [0]
                            parts.append('0')
                    # Skip closing ']'
                    if i < len(path_str) and path_str[i] == ']':
                        i += 1
                    continue
                else:
                    current += path_str[i]
                i += 1
            if current:
                parts.append(current)
            return parts

        # Try exact match first (longest tool names first)
        sorted_tools = sorted(results_by_tool.keys(), key=len, reverse=True)

        for tool_name in sorted_tools:
            # Original exact match
            if placeholder.startswith(tool_name + '.'):
                remaining = placeholder[len(tool_name) + 1:]
                field_path = parse_field_path(remaining)
                return tool_name, field_path

            # Auto-resolve dotted ↔ underscore tool names.
            # Planner generates dotted names (jira.search_users) but results
            # may be stored under sanitized names (jira_search_users), or
            # vice versa.  Try both forms to avoid false mismatches.

            # Try dotted form if stored name uses underscores
            # e.g., stored: "jira_search_users" → try matching "jira.search_users."
            dotted_form = _underscore_to_dotted(tool_name)
            if dotted_form != tool_name and placeholder.startswith(dotted_form + '.'):
                remaining = placeholder[len(dotted_form) + 1:]
                field_path = parse_field_path(remaining)
                return tool_name, field_path  # Return the ACTUAL stored key

            # Try underscore form if stored name uses dots
            # e.g., stored: "jira.search_users" → try matching "jira_search_users."
            underscore_form = tool_name.replace('.', '_')
            if underscore_form != tool_name and placeholder.startswith(underscore_form + '.'):
                remaining = placeholder[len(underscore_form) + 1:]
                field_path = parse_field_path(remaining)
                return tool_name, field_path

        # Fuzzy match: try progressively longer dot-prefixes (3, 2, 1 segments)
        # This avoids the old single-segment prefix bug where e.g. "jira" matched
        # "jira_search_users" but leaked "search_users" into the field path.
        parts = placeholder.split('.')
        if len(parts) >= MIN_PLACEHOLDER_PARTS:
            for prefix_len in range(min(len(parts) - 1, 3), 0, -1):
                prefix_candidate = '.'.join(parts[:prefix_len])
                remaining = '.'.join(parts[prefix_len:])
                field_path = parse_field_path(remaining)

                for tool_name in sorted_tools:
                    normalized_tool = tool_name.lower().replace('_', '').replace('.', '')
                    normalized_prefix = prefix_candidate.lower().replace('_', '').replace('.', '')

                    if normalized_prefix == normalized_tool:
                        return tool_name, field_path

        return None, []

    @classmethod
    def _extract_source_tool_name(cls, placeholder: str) -> str | None:
        """Extract the source tool name from a placeholder string.

        'jira.search_users.data.results[0].accountId' -> 'jira.search_users'
        'jira_search_users.data.results[0].accountId' -> 'jira_search_users'
        """
        parts = placeholder.split(".")
        if len(parts) >= 2:
            return f"{parts[0]}.{parts[1]}"
        return parts[0] if parts else None


# ============================================================================
# TOOL EXECUTION - SEQUENTIAL WITH CASCADING SUPPORT
# ============================================================================

class ToolExecutor:
    """Handles tool execution with cascading support"""

    @staticmethod
    def _format_args_preview(args: dict[str, Any], max_len: int = 220) -> str:
        """Return a compact JSON preview for tool args in logs."""
        try:
            preview = json.dumps(args, default=str, ensure_ascii=False)
        except Exception:
            preview = str(args)
        if len(preview) > max_len:
            return preview[:max_len] + "..."
        return preview

    @staticmethod
    async def execute_tools(
        planned_tools: list[dict[str, Any]],
        tools_by_name: dict[str, Any],
        llm: BaseChatModel,
        state: ChatState,
        log: logging.Logger,
        writer: StreamWriter,
        config: RunnableConfig
    ) -> list[dict[str, Any]]:
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
        planned_tools: list[dict[str, Any]],
        tools_by_name: dict[str, Any],
        llm: BaseChatModel,
        state: ChatState,
        log: logging.Logger,
        writer: StreamWriter,
        config: RunnableConfig
    ) -> list[dict[str, Any]]:
        """Execute tools sequentially with placeholder resolution"""
        from app.modules.agents.qna.tool_system import _sanitize_tool_name_if_needed

        tool_results = []
        results_by_tool = {}  # Store successful results for placeholder resolution
        tool_invocation_counts = {}  # Track how many times each tool has been called

        for i, tool_call in enumerate(planned_tools):
            tool_name = tool_call.get("name", "")
            tool_args = tool_call.get("args", {})

            # Resolve tool name: tools_by_name contains both sanitized (underscore)
            # and original (dot) names, so a direct lookup covers the common cases.
            # Fall back to sanitizing the LLM name (replaces dots→underscores) in
            # case the LLM used the dotted form but only the sanitized key is stored.
            actual_tool_name = None
            if tool_name in tools_by_name:
                actual_tool_name = tool_name
            else:
                normalized_name = _sanitize_tool_name_if_needed(tool_name, llm, state) if llm else tool_name
                if normalized_name in tools_by_name:
                    actual_tool_name = normalized_name

            if actual_tool_name is None:
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
                log.warning(f"⚠️ Unresolved placeholders in {actual_tool_name}: {unresolved} — stripping to None and proceeding (Pydantic will catch required fields)")

                # Strip unresolved placeholders to None so optional fields are
                # simply omitted and the tool can still run.  Required fields
                # that end up as None will be rejected by Pydantic validation.
                resolved_args, stripped_placeholders = PlaceholderResolver.strip_unresolved(resolved_args)
                log.debug(f"  Stripped {len(stripped_placeholders)} placeholder(s): {stripped_placeholders}")

                # Cascading dependency check: if any stripped placeholder
                # references a tool in this execution chain, it is NOT optional
                # — it means the cascading chain broke.  Skip execution.
                if stripped_placeholders:
                    cascade_failures = []
                    planned_tool_names = {t.get("name", "") for t in planned_tools}
                    planned_tool_names_sanitized = {n.replace(".", "_") for n in planned_tool_names}
                    all_known = planned_tool_names | planned_tool_names_sanitized | set(results_by_tool.keys())

                    for ph in stripped_placeholders:
                        source = PlaceholderResolver._extract_source_tool_name(ph)
                        if source and (source in all_known or source.replace(".", "_") in all_known):
                            cascade_failures.append(ph)

                    if cascade_failures:
                        log.error(
                            f"CASCADE FAILURE: {actual_tool_name} depends on unresolved "
                            f"cascading placeholders: {cascade_failures}. Skipping execution."
                        )
                        tool_results.append({
                            "tool_name": actual_tool_name,
                            "result": f"Cascade failure: dependent data not available from {cascade_failures}",
                            "status": "cascade_error",
                            "tool_id": f"call_{i}_{actual_tool_name}",
                            "orchestration_status": ORCHESTRATION_STATUS_CASCADE_BROKEN,
                        })
                        continue  # Skip execution — result would be meaningless

                # If placeholders still remain after stripping (shouldn't happen),
                # something is structurally wrong – fail the tool call.
                if PlaceholderResolver.has_placeholders(resolved_args):
                    still_unresolved = PlaceholderResolver.PLACEHOLDER_PATTERN.findall(json.dumps(resolved_args))
                    log.error(f"❌ Could not strip all placeholders in {actual_tool_name}: {still_unresolved}")

                    # Check if this is due to empty search results - provide helpful error
                    error_msg = f"Error: Unresolved placeholders: {', '.join(set(still_unresolved))}"
                    for placeholder in still_unresolved:
                        # Check if placeholder references a search that returned empty
                        if "search" in placeholder.lower() and "results[0]" in placeholder:
                            for tn in results_by_tool:
                                if tn in placeholder or placeholder.startswith(tn.split('.')[-1]):
                                    tool_data = results_by_tool[tn]
                                    if isinstance(tool_data, dict) and "data" in tool_data:
                                        data = tool_data["data"]
                                        if isinstance(data, dict) and "results" in data:
                                            results = data["results"]
                                            if isinstance(results, list) and len(results) == 0:
                                                error_msg += f" (Search '{tn}' returned empty results - check conversation history for page_id instead)"
                                                break

                    tool_results.append({
                        "tool_name": actual_tool_name,
                        "result": error_msg,
                        "status": "error",
                        "tool_id": f"call_{i}_{actual_tool_name}"
                    })
                    continue

            # Stream detailed status with context
            tool_display_name = actual_tool_name.replace("_", " ").title()
            # Extract meaningful info from tool name
            if "retrieval" in actual_tool_name.lower():
                status_msg = "Searching knowledge base for relevant information..."
            elif "confluence" in actual_tool_name.lower():
                if "create" in actual_tool_name.lower():
                    status_msg = "Creating Confluence page..."
                elif "update" in actual_tool_name.lower():
                    status_msg = "Updating Confluence page..."
                elif "get" in actual_tool_name.lower() or "search" in actual_tool_name.lower():
                    status_msg = "Retrieving Confluence content..."
                else:
                    status_msg = f"Working with Confluence: {tool_display_name}..."
            elif "jira" in actual_tool_name.lower():
                if "create" in actual_tool_name.lower():
                    status_msg = "Creating Jira issue..."
                elif "update" in actual_tool_name.lower():
                    status_msg = "Updating Jira issue..."
                elif "search" in actual_tool_name.lower() or "get" in actual_tool_name.lower():
                    status_msg = "Searching Jira for information..."
                else:
                    status_msg = f"Working with Jira: {tool_display_name}..."
            else:
                status_msg = f"Executing {tool_display_name}..."

            safe_stream_write(writer, {
                "event": "status",
                "data": {"status": "executing", "message": status_msg}
            }, config)

            log.info(
                "🛠️ Tool call [%d/%d]: %s | args=%s",
                i + 1,
                len(planned_tools),
                actual_tool_name,
                ToolExecutor._format_args_preview(resolved_args),
            )

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

            log.info(
                "📌 Tool status [%d/%d]: %s | status=%s | duration_ms=%.0f",
                i + 1,
                len(planned_tools),
                actual_tool_name,
                result_dict.get("status", "unknown"),
                float(result_dict.get("duration_ms", 0.0)),
            )

            # Send completion status
            if result_dict.get("status") == "success":
                if "retrieval" in actual_tool_name.lower():
                    completion_msg = "Knowledge base search completed"
                elif "confluence" in actual_tool_name.lower():
                    if "create" in actual_tool_name.lower():
                        completion_msg = "Confluence page created successfully"
                    elif "update" in actual_tool_name.lower():
                        completion_msg = "Confluence page updated successfully"
                    else:
                        completion_msg = "Confluence operation completed"
                elif "jira" in actual_tool_name.lower():
                    if "create" in actual_tool_name.lower():
                        completion_msg = "Jira issue created successfully"
                    elif "update" in actual_tool_name.lower():
                        completion_msg = "Jira issue updated successfully"
                    else:
                        completion_msg = "Jira operation completed"
                else:
                    completion_msg = f"{actual_tool_name.replace('_', ' ').title()} completed"

                safe_stream_write(writer, {
                    "event": "status",
                    "data": {"status": "executing", "message": completion_msg}
                }, config)
            else:
                # Tool failed - send error status
                error_msg = result_dict.get("error", "Unknown error")
                safe_stream_write(writer, {
                    "event": "status",
                    "data": {"status": "error", "message": f"Operation failed: {error_msg[:100]}"}
                }, config)

            # Set default orchestration status
            if "orchestration_status" not in result_dict:
                result_dict["orchestration_status"] = ORCHESTRATION_STATUS_RESOLVED

            # Store successful results for next placeholder resolution
            if result_dict.get("status") == "success":
                # Extract clean data for placeholder resolution
                result_data = ToolResultExtractor.extract_data_from_result(
                    result_dict.get("result")
                )

                # Track tool invocation count for multiple calls to the same tool
                if actual_tool_name not in tool_invocation_counts:
                    tool_invocation_counts[actual_tool_name] = 0
                    # Store first invocation without suffix
                    results_by_tool[actual_tool_name] = result_data
                    log.debug(f"✅ Stored result for {actual_tool_name} (keys: {list(result_data.keys()) if isinstance(result_data, dict) else type(result_data).__name__})")
                else:
                    # For subsequent invocations, store with suffix
                    tool_invocation_counts[actual_tool_name] += 1
                    suffix_number = tool_invocation_counts[actual_tool_name] + 1
                    storage_key = f"{actual_tool_name}_{suffix_number}"
                    results_by_tool[storage_key] = result_data
                    log.debug(f"✅ Stored result for {storage_key} (keys: {list(result_data.keys()) if isinstance(result_data, dict) else type(result_data).__name__})")

                # Detect empty cascade sources: if this tool returned empty
                # results and a downstream tool depends on its output via
                # placeholder, mark as a broken cascade source.
                if _is_semantically_empty(result_data):
                    dotted_name = tool_call.get("name", "")
                    remaining_tools = planned_tools[i + 1:]
                    for downstream in remaining_tools:
                        args_str = json.dumps(downstream.get("args", {}), default=str)
                        if actual_tool_name in args_str or dotted_name in args_str:
                            log.warning(
                                f"SEMANTIC FAILURE: {actual_tool_name} returned empty "
                                f"results but downstream tool depends on its output"
                            )
                            result_dict["orchestration_status"] = "empty_cascade_source"
                            break
            else:
                log.debug(f"❌ Skipped storing failed tool: {actual_tool_name}")

        return tool_results

    @staticmethod
    async def _execute_parallel(
        planned_tools: list[dict[str, Any]],
        tools_by_name: dict[str, Any],
        llm: BaseChatModel,
        state: ChatState,
        log: logging.Logger
    ) -> list[dict[str, Any]]:
        """Execute tools in parallel"""
        from app.modules.agents.qna.tool_system import _sanitize_tool_name_if_needed

        tasks = []

        for i, tool_call in enumerate(planned_tools[:NodeConfig.MAX_PARALLEL_TOOLS]):
            tool_name = tool_call.get("name", "")
            tool_args = tool_call.get("args", {})

            # Resolve tool name: same 2-step strategy as sequential executor.
            actual_tool_name = None
            if tool_name in tools_by_name:
                actual_tool_name = tool_name
            else:
                normalized_name = _sanitize_tool_name_if_needed(tool_name, llm, state) if llm else tool_name
                if normalized_name in tools_by_name:
                    actual_tool_name = normalized_name

            if actual_tool_name is None:
                log.warning(f"❌ Tool not found: {tool_name}")
                # Create error result directly
                tasks.append(asyncio.create_task(asyncio.sleep(0, result={
                    "tool_name": tool_name,
                    "result": f"Error: Tool '{tool_name}' not found",
                    "status": "error",
                    "tool_id": f"call_{i}_{tool_name}"
                })))
                continue

            log.info(
                "🛠️ Parallel tool queued [%d/%d]: %s | args=%s",
                i + 1,
                min(len(planned_tools), NodeConfig.MAX_PARALLEL_TOOLS),
                actual_tool_name,
                ToolExecutor._format_args_preview(tool_args),
            )

            # Execute tool directly (content generation happens in planner)
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
                log.info(
                    "📌 Parallel tool status: %s | status=%s | duration_ms=%.0f",
                    result.get("tool_name", "unknown"),
                    result.get("status", "unknown"),
                    float(result.get("duration_ms", 0.0)),
                )

        return tool_results

    @staticmethod
    async def _execute_single_tool(
        tool: object,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_id: str,
        state: ChatState,
        log: logging.Logger
    ) -> dict[str, Any]:
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
                if content :
                    success = True
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
        tool_args: dict[str, Any],
        log: logging.Logger
    ) -> dict[str, Any] | None:
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
    async def _run_tool(tool: object, args: dict[str, Any]) -> dict[str, Any] | str | tuple[bool, str] | list[Any] | None:
        """Run tool using appropriate method - all tools run in the same event loop as FastAPI"""
        if hasattr(tool, 'arun'):
            # Tool has async arun() - use it directly (no thread executor)
            return await tool.arun(args)
        elif hasattr(tool, '_run'):
            # Sync _run() - call directly (shouldn't happen if tools are properly async)
            # This is a fallback for backwards compatibility
            return tool._run(**args)
        else:
            # Fallback to run() method
            return tool.run(**args)

    @staticmethod
    def _process_retrieval_output(result: dict[str, Any] | str | tuple[bool, str] | list[Any] | None, state: ChatState, log: logging.Logger) -> str:
        """Process retrieval tool output and update state (accumulates results from multiple retrieval calls)"""
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
                # Accumulate final_results instead of overwriting (for parallel retrieval calls)
                existing_final_results = state.get("final_results", [])
                if not isinstance(existing_final_results, list):
                    existing_final_results = []

                # Combine new results with existing ones
                new_final_results = retrieval_output.final_results or []
                combined_final_results = existing_final_results + new_final_results
                state["final_results"] = combined_final_results

                # Accumulate virtual_record_id_to_result
                existing_virtual_map = state.get("virtual_record_id_to_result", {})
                if not isinstance(existing_virtual_map, dict):
                    existing_virtual_map = {}

                new_virtual_map = retrieval_output.virtual_record_id_to_result or {}
                combined_virtual_map = {**existing_virtual_map, **new_virtual_map}
                state["virtual_record_id_to_result"] = combined_virtual_map

                # Accumulate tool_records
                if retrieval_output.virtual_record_id_to_result:
                    existing_tool_records = state.get("tool_records", [])
                    if not isinstance(existing_tool_records, list):
                        existing_tool_records = []

                    new_tool_records = list(retrieval_output.virtual_record_id_to_result.values())
                    # Avoid duplicates by checking record IDs
                    existing_record_ids = {rec.get("_id") for rec in existing_tool_records if isinstance(rec, dict) and "_id" in rec}
                    unique_new_records = [
                        rec for rec in new_tool_records
                        if not (isinstance(rec, dict) and rec.get("_id") in existing_record_ids)
                    ]
                    combined_tool_records = existing_tool_records + unique_new_records
                    state["tool_records"] = combined_tool_records

                log.info(f"📚 Retrieved {len(new_final_results)} knowledge blocks (total: {len(combined_final_results)})")
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

### When to Use Jira API Tools

**Use `jira.search_issues` (with JQL) whenever the query contains:**
- Service-specific nouns: "tickets", "issues", "bugs", "epics", "stories", "tasks", "sprints", "backlog"
- Examples: "web connector tickets", "show login bugs", "open epics", "PA sprint issues"

**Pattern: "[topic] tickets/issues/bugs/epics"**
- "web connector tickets" → `jira.search_issues(jql="text ~ 'web connector' AND updated >= -90d")`
- "login bug issues" → `jira.search_issues(jql="text ~ 'login bug' AND updated >= -30d")`
- "open epics" → `jira.search_issues(jql="issuetype = Epic AND resolution IS EMPTY AND updated >= -90d")`

**When Jira is ALSO indexed (see DUAL-SOURCE APPS), add retrieval in parallel:**
- "web connector tickets" → retrieval(query="web connector") + jira.search_issues(jql="text ~ 'web connector' AND updated >= -90d")
- Run both in the same `tools` array (parallel execution)

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

### Pagination Handling
- When `jira.search_issues` or `jira.get_issues` returns results with `nextPageToken` or `isLast: false`, there are MORE results available
- If user asks for "all issues", "all results", "everything", or "complete list", you MUST handle pagination automatically:
  1. Check if result has `nextPageToken` field (not null/empty)
  2. If yes, call the same tool again with `nextPageToken` parameter to get next page
  3. Continue until `isLast: true` or no `nextPageToken` exists
- Use cascading tool calls for pagination:
  ```json
  {{
    "tools": [
      {{"name": "jira.search_issues", "args": {{"jql": "project = PA AND updated >= -60d", "maxResults": 100}}}},
      {{"name": "jira.search_issues", "args": {{"jql": "project = PA AND updated >= -60d", "nextPageToken": "{{{{jira.search_issues.data.nextPageToken}}}}"}}}}
    ]
  }}
  ```
- **CRITICAL**: For "all" or "complete" requests, automatically handle pagination - DO NOT ask for clarification
- Combine all results from all pages when presenting to the user
"""

CONFLUENCE_GUIDANCE = r"""
## Confluence-Specific Guidance

### Tool Selection
- CREATE page → use `confluence.create_page`
- SEARCH/FIND page → use `confluence.search_pages`
- GET/READ pages → use `confluence.get_pages_in_space` or `confluence.get_page_content`

### ⚠️ CRITICAL: Never Use Retrieval for Confluence Page Content

**NEVER use `retrieval.search_internal_knowledge` to get Confluence page content, summaries, or details.**

**WRONG - Don't use retrieval for page content:**
```json
{{
  "tools": [
    {{"name": "retrieval.search_internal_knowledge", "args": {{"query": "Confluence page 230424579 content"}}}},
    {{"name": "retrieval.search_internal_knowledge", "args": {{"query": "Overview page content"}}}}
  ]
}}
```

**CORRECT - Use `confluence.get_page_content` for page content:**
```json
{{
  "tools": [
    {{"name": "confluence.get_page_content", "args": {{"page_id": "230424579"}}}},
    {{"name": "confluence.get_page_content", "args": {{"page_id": "13238776"}}}}
  ]
}}
```

**When to use `confluence.get_page_content`:**
- ✅ User asks for "content", "summary", "details", "body", "text" of a Confluence page
- ✅ User asks to "get the content" or "read the page"
- ✅ User wants to generate summaries or extract information from pages
- ✅ User mentions specific page IDs or page names from conversation history
- ✅ Any request involving the actual content/body of a Confluence page

**When NOT to use retrieval:**
- ❌ "get the content of page X" → Use `confluence.get_page_content`
- ❌ "get content format summary" → Use `confluence.get_page_content`
- ❌ "read the page" → Use `confluence.get_page_content`
- ❌ "what's in the page" → Use `confluence.get_page_content`
- ❌ Any query about page content, even if it sounds like a knowledge query

**CRITICAL RULE:**
- If the user is asking about Confluence page CONTENT (not general knowledge), ALWAYS use `confluence.get_page_content` with the page_id
- Check conversation history or reference data for page IDs before calling the tool
- NEVER default to retrieval when page IDs are available or can be found in conversation history

### ⚠️ CRITICAL: Never Use Retrieval for IDs/Keys

**WRONG - Don't use retrieval to get page_id or space_id:**
```json
{{
  "tools": [
    {{"name": "retrieval.search_internal_knowledge", "args": {{"query": "page id"}}}},
    {{"name": "confluence.update_page", "args": {{"page_id": "{{{{retrieval.search_internal_knowledge.data.results[0].id}}}}"}}}}
  ]
}}
```

**CORRECT - Use Confluence tools to get IDs:**
```json
{{
  "tools": [
    {{"name": "confluence.search_pages", "args": {{"title": "My Page"}}}},
    {{"name": "confluence.update_page", "args": {{"page_id": "{{{{confluence.search_pages.data.results[0].id}}}}"}}}}
  ]
}}
```

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
1. **Check Reference Data first** - if `type: "confluence_space"` exists, use its `id` field directly (NO placeholders)
2. **If user provided space_id directly** - use it directly (NO placeholders)
3. **If space_id needs to be resolved from space key/name**:
   - **ONLY THEN** use cascading: Call `confluence.get_spaces` first, then use placeholder in `create_page`
   - Example (cascading): `[{"name": "confluence.get_spaces"}, {"name": "confluence.create_page", "args": {"space_id": "{{{{confluence.get_spaces.data.results[0].id}}}}", ...}}]`
4. **CRITICAL**: API requires numeric space IDs. Always use `id` field, never `key` field.
5. **CRITICAL**: If space_id is already known (from user input or reference data), use it directly - DO NOT use placeholders

### Page ID Resolution for update_page/get_page_content

**⚠️ CRITICAL: Handle empty search results gracefully**

**BEFORE using placeholders, check these in order:**

1. **Check conversation history FIRST** - If a page was just created or mentioned:
   - Look for previous assistant messages that created/mentioned the page
   - Extract the page_id from those messages
   - Use it directly (NO placeholders)
   - Example: User says "update the page I just created" → Find page_id from create_page result in conversation history

2. **If user provided page_id directly** - use it directly (NO placeholders)

3. **If you MUST search for a page** (only if not in conversation history):
   - Use cascading: Call `confluence.search_pages` first
   - **BUT**: Be aware that search might return empty results
   - **If search returns empty**: The placeholder will FAIL - you need to handle this
   - **Better approach**: If page might not exist, check conversation history first, or use `confluence.get_pages_in_space` to list pages

**Example - Using conversation history (RECOMMENDED):**
```json
{{
  "tools": [{{
    "name": "confluence.update_page",
    "args": {{
      "page_id": "230588418",  // From conversation history - page was just created
      "page_content": "<h1>Updated Content</h1>..."
    }}
  }}]
}}
```

**Example - Cascading (only if page_id not in conversation history):**
```json
{{
  "tools": [
    {{"name": "confluence.search_pages", "args": {{"title": "My Page", "space_id": "123"}}}},
    {{"name": "confluence.get_page_content", "args": {{"page_id": "{{{{confluence.search_pages.data.results[0].id}}}}"}}}}
  ]
}}
```

**⚠️ IMPORTANT**:
- If `confluence.search_pages` returns empty results (`results: []`), the placeholder will FAIL
- **ALWAYS check conversation history first** before using search with placeholders
- If user says "update the page I just created" → Use page_id from conversation history, NOT a search
"""

SLACK_GUIDANCE = r"""
## Slack-Specific Guidance

### Tool Selection — Use the Right Slack Tool for Every Task

| User intent | Correct Slack tool | Key parameters |
|---|---|---|
| Send message to a channel | `slack.send_message` | `channel` (name or ID), `message` |
| Send a direct message (DM) | `slack.send_direct_message` | `user_id` or `email`, `message` |
| Reply to a thread | `slack.reply_to_message` | `channel`, `thread_ts`, `message` |
| Set my Slack status | `slack.set_user_status` | `status_text`, `status_emoji`, `duration_seconds` |
| Get channel messages / history | `slack.get_channel_history` | `channel` |
| List my channels | `slack.get_user_channels` | (no required args) |
| Get channel info | `slack.get_channel_info` | `channel` |
| Search messages | `slack.search_messages` or `slack.search_all` | `query` |
| Get user info | `slack.get_user_info` | `user_id` or `email` |
| Schedule a message | `slack.schedule_message` | `channel`, `message`, `post_at` (Unix timestamp) |
| Add reaction to message | `slack.add_reaction` | `channel`, `timestamp`, `name` |

**R-SLACK-1: NEVER use `retrieval.search_internal_knowledge` for any Slack query.**
Slack queries always use Slack service tools, not retrieval.
- ❌ "What are my Slack channels?" → Do NOT use retrieval → ✅ Use `slack.get_user_channels`
- ❌ "Messages in #random" → Do NOT use retrieval → ✅ Use `slack.get_channel_history`
- ❌ "Search Slack for X" → Do NOT use retrieval → ✅ Use `slack.search_messages`

**R-SLACK-2: `slack.set_user_status` uses `duration_seconds`, NOT a Unix timestamp.**
The tool calculates the expiry time internally. You provide how many seconds from now.
- ❌ WRONG: Use calculator to compute Unix timestamp → then pass to `expiration` field
- ✅ CORRECT: Pass `duration_seconds` directly (e.g., `3600` for 1 hour)

Duration reference:
- 15 min → `900` | 30 min → `1800` | 1 hour → `3600` | 2 hours → `7200` | 4 hours → `14400` | 1 day → `86400`
- No expiry → omit `duration_seconds` entirely

Correct single-tool call:
```json
{"name": "slack.set_user_status", "args": {"status_text": "In a meeting", "status_emoji": ":calendar:", "duration_seconds": 3600}}
```

**R-SLACK-3: Channel identification.**
Pass channel names with `#` prefix (`"#general"`) or channel IDs from Reference Data. If Reference Data has a `slack_channel` entry, use its `id` field directly as the `channel` parameter.

**R-SLACK-4: Cross-service cascade — fetch from another service, post to Slack.**
When the user asks to fetch data from Confluence/Jira/etc. AND post it to a Slack channel, plan BOTH tools in sequence.

Pattern: "[fetch data from Service A] and post/share/send it to [Slack channel]"

Step 1 → fetch with the appropriate service tool
Step 2 → `slack.send_message` with a **human-readable, clean text message** you write inline

Key rules:
- Always fetch FIRST, send SECOND
- The Slack `message` field must be **plain text or Slack mrkdwn** — never raw JSON, never raw HTML
- If channel is in Reference Data, use its `id` directly
- NEVER use retrieval to "look up" Confluence/Jira data — use the real service tool

**R-SLACK-5: NEVER pass raw tool output directly as the Slack `message` body.**

Slack accepts **plain text** or **Slack mrkdwn** (using `*bold*`, `_italic_`, `` `code` ``, `• bullet`).

These formats are INCOMPATIBLE with Slack — do NOT pass them as message body:
- ❌ Confluence storage HTML (`<h1>`, `<p>`, `<ul>`, `&mdash;`, `&lt;`, HTML entities)
- ❌ Raw JSON or dict objects
- ❌ Any tool output containing HTML tags or unescaped special characters

**The LLM must always WRITE the Slack message text itself.** Think of it as: "what would a human type into Slack?" — short, readable, no HTML.

**Placeholders are for IDENTIFIERS only** (IDs, keys, names, tokens from lookup tools):
- ✅ Use placeholder: `{{confluence.get_spaces.data.results[0].name}}` — this resolves to a plain string like "Engineering"
- ✅ Use placeholder: `{{confluence.search_pages.data.results[0].id}}` — resolves to a numeric ID
- ❌ Do NOT use: `{{confluence.get_page_content.data.content}}` — this resolves to raw HTML which Slack cannot render

**Correct cross-service pattern:**

When "fetch Confluence content → summarize → post to Slack":
1. Use `confluence.search_pages` or `confluence.get_page_content` to fetch the content
2. **Write a clean text summary yourself** as the `message` value for Slack — do NOT placeholder the content
3. The summary should use Slack mrkdwn format (bullets with `•`, bold with `*`, code with `` ` ``)

**Example — "list my Confluence spaces and post to #starter"** (structured data → fine to use field placeholders):
```json
{
  "tools": [
    {"name": "confluence.get_spaces", "args": {}},
    {"name": "slack.send_message", "args": {
      "channel": "#starter",
      "message": "Here are our Confluence spaces:\n• {{confluence.get_spaces.data.results[0].name}} (key: {{confluence.get_spaces.data.results[0].key}})\n• {{confluence.get_spaces.data.results[1].name}} (key: {{confluence.get_spaces.data.results[1].key}})"
    }}
  ]
}
```

**Example — "summarize Confluence page and post to Slack"** (page content → must write summary yourself):
```json
{
  "tools": [
    {"name": "confluence.get_page_content", "args": {"page_id": "231440385"}},
    {"name": "slack.send_message", "args": {
      "channel": "#starter",
      "message": "*Page Summary: Space Summary — PipesHub Deployment*\n\n• PipesHub connects enterprise tools (Slack, Jira, Confluence, Google Workspace) with natural-language search and AI agents.\n• Deployment: run from `pipeshub-ai/deployment/docker-compose`; configure env vars in `env.template`.\n• Stop production stack: `docker compose -f docker-compose.prod.yml -p pipeshub-ai down`\n• Supports real-time and scheduled indexing modes.\n\n_Full page: https://your-domain.atlassian.net/wiki/..._"
    }}
  ]
}
```

Notice: the `message` is written entirely by the LLM as clean Slack text — the page content placeholder is NOT used for the message body.

**When the task says "make a summary and post to Slack":**
- The "summary" is your JOB to write — read the page content (step 1), then compose a clean bullet-point summary (step 2)
- Slack cannot render HTML; you must convert to plain readable text
- Keep it concise (8–15 bullets max); if the page is long, highlight the key points

**R-SLACK-6: NEVER cascade to `slack.resolve_user` after search tools.**

Slack search results (`slack.search_messages`, `slack.search_all`) **already include user information** (username, display name, user ID) in the response. There is NO need to cascade to `slack.resolve_user` to get user details.

**WRONG — unnecessary cascade to resolve_user:**
```json
{
  "tools": [
    {"name": "slack.search_messages", "args": {"query": "product updates"}},
    {"name": "slack.resolve_user", "args": {"user_id": "{{slack.search_messages.data.messages[0].user}}"}}
  ]
}
```

**CORRECT — search results already contain username:**
```json
{
  "tools": [
    {"name": "slack.search_messages", "args": {"query": "product launch"}}
  ]
}
```

The search response structure already includes:
- `username` field — the user's display name (e.g., "abhishek", "john.doe")
- `user` field — the Slack user ID (e.g., "U1234567890")
- Both are directly available in the search results without additional tool calls

**When to use `slack.resolve_user`:**
- ✅ When you ONLY have a user ID and need to get their full name/email for display
- ✅ When processing data from non-Slack sources that only provide user IDs
- ❌ NOT after search_messages, search_all, or get_channel_history — these already include user info

**R-SLACK-7: DM conversation history — use `get_channel_history`, NOT search.**

When the user asks for "conversations between me and [person]" or "DM history with [person]" for a time period:

**WRONG — using search (incomplete results, wrong tool):**
```json
{
  "tools": [
    {"name": "slack.search_all", "args": {"query": "from:@abhishek"}}
  ]
}
```
❌ Search returns limited results (default 20), not complete conversation history
❌ Search is for FINDING messages by content/keyword, not retrieving conversation history

**CORRECT — get complete DM history:**
```json
{
  "tools": [
    {"name": "slack.get_user_conversations", "args": {"types": "im"}},
    {"name": "slack.get_channel_history", "args": {"channel": "D07QDNW518E", "limit": 1000}}
  ]
}
```
✅ `get_user_conversations` finds all DM channels
✅ `get_channel_history` retrieves complete conversation thread (up to 1000 messages)
✅ If you already know the DM channel ID from Reference Data, skip step 1

**Query pattern recognition:**
- "conversations between me and X" → `get_channel_history` on the DM channel
- "messages with X for last N days" → `get_channel_history` with time filter (if available) or high limit
- "chat history with X" → `get_channel_history` on the DM channel
- "what did X and I discuss" → `get_channel_history` on the DM channel

**Never do this:**
- ❌ Tell the user "I need you to call slack.get_channel_history"
- ❌ Tell the user "share the output of tool X"
- ❌ Explain what tools the user should run
- ✅ YOU execute the tools yourself to get complete data

If the DM channel ID is not in Reference Data:
1. Call `slack.get_user_conversations(types="im")` to find all DM channels
2. Identify the correct DM by matching user IDs in the conversation member list
3. Call `slack.get_channel_history` on that channel ID

**Time filtering:**
The Slack `conversations.history` API doesn't support date-based filtering directly, but you can:
- Request a high `limit` (e.g., 1000 messages) to ensure you capture the last N days
- The response includes timestamps — filter/analyze timestamps in the response
- For "last 10 days", requesting 1000 messages typically covers it for most DMs

**Complete example — "conversations between me and X for last 10 days":**

Scenario: User asks "want to know about conversations had between me and abhishek for last 10 days in private dm"

**WRONG approach (incomplete data, tells user what to do):**
```json
{
  "tools": [
    {"name": "slack.search_all", "args": {"query": "from:@abhishek"}}
  ]
}
```
Problems:
- ❌ Search only returns 20 results (page 1)
- ❌ Not a complete conversation thread
- ❌ Respond node will tell user "call slack.get_channel_history to get full data"
- ❌ User cannot and should not run tools

**CORRECT approach (complete conversation history):**

*Option A: If DM channel ID is in Reference Data (e.g., `slack_channel` type with id `D07QDNW518E`):*
```json
{
  "tools": [
    {"name": "slack.get_channel_history", "args": {"channel": "D07QDNW518E", "limit": 1000}}
  ]
}
```

*Option B: If DM channel ID not known:*
```json
{
  "tools": [
    {"name": "slack.get_user_info", "args": {"user": "abhishek"}},
    {"name": "slack.get_user_conversations", "args": {"types": "im"}},
    {"name": "slack.get_channel_history", "args": {"channel": "<DM_CHANNEL_ID_FROM_STEP_2>", "limit": 1000}}
  ]
}
```

After getting history, the respond node will:
1. Filter messages by timestamp to "last 10 days"
2. Identify key topics, action items, priorities
3. Format a summary for the user
4. NEVER tell the user to run more tools
"""

TEAMS_GUIDANCE = r"""
## Microsoft Teams-Specific Guidance

### Tool Selection — Use the Right Teams Tool for Every Task

| User intent | Correct Teams tool | Key parameters |
|---|---|---|
| List my teams | `teams.get_teams` | `top` (optional) |
| Get one team details | `teams.get_team` | `team_id` |
| List channels in a team | `teams.get_channels` | `team_id`, `top` (optional) |
| Create a team | `teams.create_team` | `display_name`, `description` (optional) |
| Delete a team | `teams.delete_team` | `team_id` |
| Create channel in a team | `teams.create_channel` | `team_id`, `display_name`, `description`, `channel_type` |
| Update channel info | `teams.update_channel` | `team_id`, `channel_id`, `display_name`/`description` |
| Delete channel | `teams.delete_channel` | `team_id`, `channel_id` |
| Send message to channel | `teams.send_message` | `team_id`, `channel_id`, `message` |
| Read channel messages | `teams.get_channel_messages` | `team_id`, `channel_id`, `top` (optional) |
| Create 1:1/group chat | `teams.create_chat` | `chat_type`, `member_user_ids`, `topic` (group only) |
| Get chat details | `teams.get_chat` | `chat_id` |
| Add team member | `teams.add_member` | `team_id`, `user_id`, `role` |
| Remove team member | `teams.remove_member` | `team_id`, `membership_id` |
| List meetings for a period | `teams.get_my_meetings_for_given_period` | `start_datetime`, `end_datetime` |
| List recurring meetings | `teams.get_my_recurring_meetings` | `top` (optional) |
| Create a meeting/event | `teams.create_event` | `subject`, `start_datetime`, `end_datetime` |
| Schedule a channel meeting | `teams.create_channel_meeting` | `team_id`, `channel_name`, `subject`, `start_datetime`, `end_datetime`, `timezone` (optional) |
| Edit a meeting/event | `teams.edit_event` | `event_id`, fields to update |
| Delete a meeting/event | `teams.delete_event` | `event_id` |
| Get meeting transcript | `teams.get_my_meetings_transcript` | `meeting_id` |
| Get people invited | `teams.get_people_invited` | `meeting_id` |
| Get people who attended | `teams.get_people_attended` | `meeting_id` |
| Search messages | `teams.search_messages` | `query`, `top_per_channel` (optional) |
| Reply to a message | `teams.reply_to_message` | `team_id`, `channel_id`, `parent_message_id`, `message` |

---

## R-TEAMS-0: Universal Data Resolution Hierarchy (CRITICAL — applies to EVERY tool call)

Before executing any tool, every required parameter must be resolved. Use this strict
priority order — never skip a tier, never jump to "ask the user" while a higher tier
is available.

### Resolution Tiers (evaluate in order):

**Tier 1 — Explicit in the current message**
The user stated the value directly.
→ "get transcript of the sprint planning" → meeting keyword = "sprint planning"
→ "send summary to #test" → Slack channel = #test
→ "day before yesterday's meetings" → date = 2 days ago

**Tier 2 — Derivable from the current message**
The value isn't stated but can be computed from what was said.
→ "yesterday" = yesterday's date 00:00–23:59
→ "day before yesterday" = 2 days ago 00:00–23:59
→ "this week" = Monday 00:00 to Sunday 23:59
→ "last 3 days" = 3 days ago to today
→ "slack test channel" = `#test`
→ "the engineering channel" = `#engineering`
Never ask the user to restate something you can compute or interpret yourself.

**Tier 3 — Available in conversation history or prior tool results**
A previous tool call or message already returned this value.
→ meeting_id was returned in the last search → reuse it, don't re-fetch
→ user said "that meeting" → the one from the previous turn
→ transcript was just fetched → use it for summary, don't re-fetch
Always check conversation history before making a redundant API call.

**Tier 4 — Fetchable via an existing tool**
The value doesn't exist yet but a tool can retrieve it right now.
→ Need meeting_id? → call `teams.get_my_meetings_for_given_period` first
→ Need team_id? → call `teams.get_teams` first
→ Need channel_id? → call `teams.get_channels` first
→ Need a transcript? → call `teams.get_my_meetings_transcript`
This is the fetch-before-ask rule. If a tool can get it, USE the tool.

**Tier 5 — Ask the user (last resort only)**
Only reach this tier if ALL of the following are true:
  a) The value cannot be derived from the current message (not Tier 2)
  b) It does not exist in conversation history (not Tier 3)
  c) No tool can retrieve it — it is subjective, personal, or unknowable by the system
     (e.g., "which Slack channel?" when no channel was mentioned at all)
When asking, ask for ALL missing Tier-5 values in a single message. Never ask one
at a time across multiple turns.

---

### Applied to common Teams patterns:

| Missing value | Wrong (jump to Tier 5) | Correct tier |
|---|---|---|
| meeting_id for "yesterday's meeting" | Ask user for meeting ID | Tier 4: `get_my_meetings_for_given_period` with yesterday's dates |
| meeting_id for "the sprint planning" | Ask user which meeting | Tier 4: `get_my_meetings_for_given_period` + filter by subject |
| team_id for "the Engineering team" | Ask user for team ID | Tier 4: `get_teams` then match by name |
| channel_id for "#general" | Ask user for channel ID | Tier 4: `get_channels` then match by name |
| transcript for a meeting | Ask user if they want it | Tier 4: `get_my_meetings_transcript` with meeting_id |
| date for "yesterday's meetings" | Ask user for dates | Tier 2: compute yesterday = current date - 1 day |
| Slack channel for "send to slack test" | Ask user which channel | Tier 2: interpret "slack test" = `#test` |
| Slack channel for "send to Slack" (nothing else) | — | Tier 5: genuinely missing, ask |
| Which meetings when user says "all from yesterday" | Ask user which specific one | Tier 2: "all" = process every meeting from that date |

### The Fetch-Before-Ask Decision Tree:
Is the value stated or computable from the user's message?
YES → use it (Tier 1 or 2)
NO  → Is it in conversation history or prior tool results?
YES → use it (Tier 3)
NO  → Does any available tool return this kind of data?
YES → call that tool now, then proceed (Tier 4)
NO  → ask the user (Tier 5)

Ambiguity rule (applies at any tier): if lookup/matching returns multiple users,
channels, or meetings with the same name/subject and you cannot uniquely resolve
the target, ask the user for specific clarification and one unique value.
Never execute the task using the first match or a randomly selected match.

This hierarchy is non-negotiable. Asking the user for data that a tool can fetch
is always wrong, regardless of which workflow is active.

---

**R-TEAMS-1: NEVER use retrieval for live Teams data/actions.**
- ❌ "Show my Teams channels" → Do NOT use retrieval → ✅ Use `teams.get_channels`
- ❌ "Post message in Teams" → Do NOT use retrieval → ✅ Use `teams.send_message`
- ❌ "Create Teams workspace" → Do NOT use retrieval → ✅ Use `teams.create_team`
- ❌ "Get my meetings" → Do NOT use retrieval → ✅ Use `teams.get_my_meetings_for_given_period`

**R-TEAMS-2: Resolve IDs before action tools.**
Action tools need exact IDs (`team_id`, `channel_id`, `chat_id`, `meeting_id`, `membership_id`).
- If the user gives names only, first fetch IDs with lookup tools (`teams.get_teams`, `teams.get_channels`, `teams.get_my_meetings_for_given_period`)
- Use placeholders only in multi-tool cascades
- NEVER ask the user for internal IDs — they don't know them

**R-TEAMS-3: Send channel messages with IDs, not names.**
`teams.send_message` requires both `team_id` and `channel_id`.
- If channel/team IDs are not already known, lookup first:
```json
{
  "tools": [
    {"name": "teams.get_teams", "args": {}},
    {"name": "teams.get_channels", "args": {"team_id": "{{teams.get_teams.data.results[0].id}}"}},
    {"name": "teams.send_message", "args": {"team_id": "{{teams.get_teams.data.results[0].id}}", "channel_id": "{{teams.get_channels.data.results[0].id}}", "message": "Update posted"}}
  ]
}
```

**R-TEAMS-4: Member removal uses `membership_id`, not `user_id`.**
For `teams.remove_member`, pass a conversation membership identifier. Do not pass a raw user ID.

**R-TEAMS-5: `create_chat.chat_type` must be `oneOnOne` or `group`.**
- Use `oneOnOne` for direct messages
- Use `group` when multiple members or a topic is required

**R-TEAMS-6: Meeting fetching — choose the right tool based on query type.**
- **Date-based** ("get my meetings for yesterday") → `teams.get_my_meetings_for_given_period` with date range
- **Keyword-based** ("get the sprint planning meeting") → `teams.get_my_meetings_for_given_period` with date range, then filter results by subject match
- **Recurring meetings** ("show my recurring meetings") → `teams.get_my_recurring_meetings`
- **Ambiguous** ("get my meetings" with no date or keyword) → Ask ONLY for the date range
- **With attendee filter** ("meetings with alice@company.com") → Fetch by date, filter results by attendee

**R-TEAMS-7: Transcript handling.**
- `teams.get_my_meetings_transcript` requires `meeting_id` — get this from meeting fetch results
- If the transcript tool returns empty or an error → report: "No transcript available for [Meeting Name]." Do NOT fabricate.
- If the meeting wasn't a Teams online meeting → no transcript is possible. Report it.
- When processing multiple meetings, attempt ALL transcripts. Report which succeeded and which didn't. Do NOT ask the user to pick — process everything, report exceptions.

**R-TEAMS-8: Summary generation from transcripts.**
When generating a summary from a transcript (you write this, NOT a tool call), include:
- Meeting title + date/time
- Attendees present
- Key discussion points
- Decisions made
- Action items (who, what, deadline if mentioned)
- Open questions / follow-ups

Rules:
- Do NOT over-summarize. Someone who missed the meeting should understand what happened.
- Do NOT hallucinate — only include information from the transcript.
- If user specified a focus ("just action items"), prioritize that but still overview other topics.

**R-TEAMS-9: Multi-meeting processing — process ALL, report exceptions.**
When the user asks about multiple meetings ("yesterday's meetings", "this week's meetings"):
- Fetch ALL meetings for the date range.
- For each meeting, attempt the requested action (transcript, summary, etc.).
- Do NOT ask the user to pick which meetings. Process all of them.
- Report results and exceptions at the end:
  - ✅ meetings that succeeded
  - ℹ️ meetings with no transcript / no Teams link
  - ❌ meetings that failed

---

### Common Planning Patterns:

**Pattern: Search and reply to a Teams message thread**
```json
{
  "tools": [
    {"name": "teams.search_messages", "args": {"query": "Q4 report", "top_per_channel": 20}},
    {"name": "teams.reply_to_message", "args": {
      "team_id": "{{teams.search_messages.data.results[0].team_id}}",
      "channel_id": "{{teams.search_messages.data.results[0].channel_id}}",
      "parent_message_id": "{{teams.search_messages.data.results[0].id}}",
      "message": "Thanks for sharing this. I will review and follow up by EOD."
    }}
  ]
}
```

**Pattern: Create a one-time meeting**
```json
{
  "tools": [
    {"name": "teams.create_event", "args": {
      "subject": "Team Sync",
      "start_datetime": "2026-03-05T14:00:00",
      "end_datetime": "2026-03-05T15:00:00",
      "timezone": "India Standard Time",
      "description": "Weekly sync for project updates.",
      "is_online_meeting": true
    }}
  ]
}
```

**Pattern: Schedule a meeting for a channel**
```json
{
  "tools": [
    {"name": "teams.create_channel_meeting", "args": {
      "team_id": "00000000-0000-0000-0000-000000000000",
      "channel_name": "Engineering",
      "subject": "Sprint Planning",
      "start_datetime": "2026-03-10T10:00:00",
      "end_datetime": "2026-03-10T11:00:00",
      "timezone": "Asia/Kolkata"
    }}
  ]
}
```

**Pattern: Get recurring meetings**
```json
{
  "tools": [
    {"name": "teams.get_my_recurring_meetings", "args": {"top": 25}}
  ]
}
```

**Pattern: Get meetings for a given period**
```json
{
  "tools": [
    {"name": "teams.get_my_meetings_for_given_period", "args": {
      "start_datetime": "2026-03-01T00:00:00",
      "end_datetime": "2026-03-07T23:59:59",
      "top": 100
    }}
  ]
}
```

**Pattern: Reschedule a meeting by searching first**
```json
{
  "tools": [
    {"name": "teams.get_my_meetings_for_given_period", "args": {
      "start_datetime": "2026-03-01T00:00:00",
      "end_datetime": "2026-03-07T23:59:59"
    }},
    {"name": "teams.edit_event", "args": {
      "event_id": "{{teams.get_my_meetings_for_given_period.data.results[0].id}}",
      "start_datetime": "2026-03-04T15:00:00",
      "end_datetime": "2026-03-04T16:00:00",
      "timezone": "India Standard Time"
    }}
  ]
}
```

**Pattern: Get transcript for a meeting**
```json
{
  "tools": [
    {"name": "teams.get_my_meetings_for_given_period", "args": {
      "start_datetime": "2026-03-04T00:00:00",
      "end_datetime": "2026-03-04T23:59:59"
    }},
    {"name": "teams.get_my_meetings_transcript", "args": {
      "meeting_id": "{{teams.get_my_meetings_for_given_period.data.results[0].id}}"
    }}
  ]
}
```

**Pattern: Compare invited vs attended people**
```json
{
  "tools": [
    {"name": "teams.get_my_meetings_for_given_period", "args": {
      "start_datetime": "2026-03-03T00:00:00",
      "end_datetime": "2026-03-03T23:59:59"
    }},
    {"name": "teams.get_people_invited", "args": {"meeting_id": "{{teams.get_my_meetings_for_given_period.data.results[0].id}}"}},
    {"name": "teams.get_people_attended", "args": {"meeting_id": "{{teams.get_my_meetings_for_given_period.data.results[0].id}}"}}
  ]
}
```

**Pattern: Full workflow — meeting → transcript → summary → Slack**
1. Fetch meetings: `teams.get_my_meetings_for_given_period`
2. Get transcript: `teams.get_my_meetings_transcript` for each meeting
3. YOU (the LLM) generate the summary — this is NOT a tool call
4. Send to Slack: `slack.send_message(channel="...", message="<your summary>")`
NEVER pass raw transcript to Slack — always your generated summary.
"""
GITHUB_GUIDANCE = r"""
## GitHub-Specific Guidance

### CRITICAL: Owner context first — never pass "me" to list_repositories or repo-scoped tools
- **Only** `github.get_owner` accepts **owner=`me`** (to get the authenticated user's profile). The response includes **login** (the actual username).
- **All other tools** (list_repositories, list_issues, get_repository, get_issue, list_pull_requests, etc.) require the **actual GitHub login** (username or org name). **Never** pass the literal "me" to these tools — the API will fail with 404.
- When the user says "my repos", "my issues", "my repo X", etc.: **first** call **`github.get_owner`(owner=`me`)** to get the authenticated user; then use the **`login`** from that response as **user** or **owner** in every subsequent tool call.
- Do NOT ask the user "What is your GitHub username?" when they said "my" — resolve it by calling get_owner(owner="me").

### Parameter rules
- **get_owner(owner, owner_type):** Use **owner=`me`** only here to get the authenticated user; response has **login**. For another user use owner=username; for an org use owner=orgname and owner_type="organization".
- **list_repositories(user, type, per_page, page):** **user** must be a real login (from get_owner result or message). **type**: "owner", "all", "member". Never pass "me". **per_page**: default 10 when omitted, max 50. **page**: default 1. For "give all repos" / "my repos" plan only **one** list_repositories call (returns first 10 by default); do not plan multiple pages.
- **Repo-scoped tools** (get_repository, list_issues, get_issue, create_issue, update_issue, close_issue, list_issue_comments, get_issue_comment, create_issue_comment, get_pull_request, get_pull_request_commits, get_pull_request_file_changes, get_pull_request_reviews, create_pull_request_review, list_pull_requests, create_pull_request, merge_pull_request, list_pull_request_comments, create_pull_request_review_comment, edit_pull_request_review_comment): **owner** and **repo** must be real values. Never pass "me" as owner.
- **list_issues(owner, repo, state, labels, assignee, per_page, page):** Returns **only issues** (pull requests are excluded). state = "open" | "closed" | "all". Use labels/assignee when user filters. per_page (default 10, max 50), page (default 1) for pagination.
- **list_pull_requests(owner, repo, state, head, base):** state = "open" | "closed" | "all". head/base filter by branch when needed.
- **search_repositories(query):** Use for "find repos about X", "search for Python repos". Query examples: "machine learning", "language:python stars:>100", "react in:name".
- **create_repository(name, private, description, auto_init):** Creates a repo under the authenticated user. Do NOT call get_owner before create_repository. **name** is the only required param (take from the user query). For anything not said: **private** = true, **description** = omit, **auto_init** = true. Never ask the user for private/public, description, or README — use defaults and run the tool.

### Tool Selection — Use the Right GitHub Tool for Every Task

| User intent | Correct GitHub tool | Key parameters |
|---|---|---|
| Who am I? / My profile / Get my login for later steps | `github.get_owner` | owner=`me`, owner_type=user |
| Another user's or org's profile | `github.get_owner` | owner=username or orgname, owner_type=user or organization |
| List my repos / Give all my repositories | First get_owner(me), then `github.list_repositories` | user=<login from get_owner>, type=owner |
| List repos for user X (e.g. darshangodase) | `github.list_repositories` | user=darshangodase (no get_owner needed) |
| Get one repo details (owner/repo known or from list) | `github.get_repository` | owner, repo |
| Create a new repository | `github.create_repository` | name only (from query). No get_owner. Defaults: private=true, description=omit, auto_init=true. Never ask for optional fields. |
| List issues in a repo (open/closed/all) | `github.list_issues` | owner, repo, state (open/closed/all), optional labels, assignee |
| Get issue
| List / Get / Create **issue comments** | `list_issue_comments` / `get_issue_comment` / `create_issue_comment` | owner, repo, number (issue); comment_id for get |
| List PRs in a repo / Get PR
| **Review this PR** / **What changes in this PR** / What files changed | `github.get_pull_request_file_changes` | owner, repo, number (PR). Returns list of changed files with path, status, additions, deletions. |
| PRs on repo X / Give PR on repo <name> (user gave repo name) | get_owner(me) then `github.list_pull_requests` | owner=<login>, repo=<name user said>. Do NOT use search_repositories. |
| **Reviews on a PR** (who approved / requested changes) | `github.get_pull_request_reviews` | owner, repo, number. Returns who approved, requested changes, or left a review comment. |
| **Submit a PR review** (approve / request changes / comment) | `github.create_pull_request_review` | owner, repo, number; event (APPROVE | REQUEST_CHANGES | COMMENT; default COMMENT — omit for general comment); optional body. Use to approve a PR (event=APPROVE), request changes (event=REQUEST_CHANGES, body recommended), or leave a general review comment (event=COMMENT or omit). |
| List / Create / Edit **PR review comments** (line-level) | `list_pull_request_comments` / `create_pull_request_review_comment` (new comment) / `edit_pull_request_review_comment` | New comment: call **get_pull_request_commits**, then set commit_id to **{{github.get_pull_request_commits.last_commit_sha}}** (do not use data[-1].sha). Then create_pull_request_review_comment(owner, repo, number, body, commit_id, path, line). |
| Search GitHub for repos by keyword | `github.search_repositories` | query (e.g. "python web framework") |

### Query → Tool flow (examples)

**"My repos" / "Give all repos" / "List my repositories":** get_owner(owner="me") → **one** list_repositories(user=<login>, type="owner"). Default is 10 repos per page (max 50). Omit per_page/page for default 10; do not plan multiple list_repositories calls.
**"My issues in repo X" / "Open issues in my repo portfolio_new":** get_owner(owner="me") → list_issues(owner=<login>, repo="X" or "portfolio_new", state="open" or "all").
**"List my PRs" (no repo named):** get_owner(owner="me") → list_repositories(user=<login>) → for each repo (or first): list_pull_requests(owner=<from full_name>, repo=<from full_name>, state="open").
**"Give PR on repo X" / "PRs on repo <name>" / "pull requests in repo <name>" (user names the repo):** get_owner(owner="me") → list_pull_requests(owner=<login from get_owner>, repo=<name user said>, state="open" or "all"). Do NOT use search_repositories — the user already gave the repo name; use it as **repo** and **owner** = login from get_owner. Only two tools: get_owner, then list_pull_requests.
**"Summarize my open issues" (no repo):** get_owner(owner="me") → list_repositories(user=<login>) → list_issues(owner, repo, state="open") for relevant repo(s); then summarize from results.
**"Create a repo" / "Create repository X" / "New GitHub repo":** Plan exactly one tool: `github.create_repository` with name from the query. Do NOT call get_owner. Do NOT set needs_clarification. Use defaults for private, description, auto_init. Never ask the user for private/public, description, or README.
**"Get repo darshangodase/portfolio_new" (explicit owner/repo):** get_repository(owner="darshangodase", repo="portfolio_new"). No get_owner needed.
**"Issues in darshangodase/portfolio_new" (explicit):** list_issues(owner="darshangodase", repo="portfolio_new", state="open" or "all"). No get_owner needed.
**"Find repos about machine learning" / "Search for Python repos":** search_repositories(query="machine learning" or "language:python"). Optionally then get_repository(owner, repo) for a chosen result using full_name.
**"Who is pipeshub-ai on GitHub?" (org or user):** get_owner(owner="pipeshub-ai", owner_type="organization" or "user").
**"Comments on issue
**"Add a comment to issue
**"Reviews on PR
**"Reviews on my PRs" / "Reviews on repo X PRs" (no "every"/"all"):** get_owner → list_pull_requests → **one** get_pull_request_reviews(owner, repo, number=**list_pull_requests.data[0].number**).

**"Give repo details then all PR details and every PR reviews" / "Every PR reviews" / "All PRs and every PR reviews":** get_owner(owner="me") → get_repository(owner, repo) → list_pull_requests(owner, repo, state="all") → then **one get_pull_request_reviews per PR**: plan get_pull_request_reviews(owner, repo, number=**list_pull_requests.data[0].number**), get_pull_request_reviews(owner, repo, number=**list_pull_requests.data[1].number**), … up to **list_pull_requests.data[9].number** (indices 0–9). Executor skips steps where the index does not exist.
**"Review comments on PR
**"Comment on this PR" / "Add a review comment on PR
**"Review this PR" / "What changes in this PR?" / "What files changed in PR
**"Approve this PR" / "Approve PR
**"Request changes on this PR" / "Request changes on PR
**"Leave a review on this PR" / "Submit a review" / "Add a general review comment" (no specific line):** create_pull_request_review(owner, repo, number, body="...") — event defaults to COMMENT; omit event for a general comment.

### Rules (R-GITHUB)

**R-GITHUB-1: For "my" (authenticated user) — always get owner context first.**
- "My repos" / "Give all my repos" / "List my repositories" → Step 1: get_owner(owner="me"). Step 2: **exactly one** list_repositories(user=<login>, type="owner"). Do NOT pass user="me". Default 10 repos (max 50); omit per_page/page for default. Do NOT plan multiple list_repositories for different pages.
- "My issues" / "issues in my repo X" / "list issues in my repo portfolio_new" → Step 1: get_owner(owner="me"). Step 2: list_issues(owner=<login>, repo="X" or "portfolio_new", state=...). Do NOT pass owner="me".
- "My PRs" / "pull requests in my repo Y" → Same: get_owner(owner="me") first, then list_pull_requests(owner=<login>, repo=Y). Never owner="me".

**R-GITHUB-2: Never pass "me" to list_repositories, list_issues, get_repository, or any repo-scoped tool.** The API expects a real login. Use the **login** from get_owner(owner="me") result.

**R-GITHUB-3: When repo is not specified (e.g. "list my issues" without naming a repo).**
- Step 1: get_owner(owner="me") → **login**.
- Step 2: list_repositories(user=<login>) → list of repos; each has **full_name** (owner/repo_name) and **name**.
- Step 3: For each repo (or first/few): owner = first part of full_name, repo = second part; call list_issues(owner, repo, ...) or list_pull_requests(owner, repo, ...). Do not ask the user which repo — use the list.

**R-GITHUB-4: Chaining — use previous tool results.**
- **From get_owner(owner=me):** Use the **login** field as **user** in list_repositories and as **owner** in all repo-scoped tools.
- **From list_repositories:** Each item has **full_name** (e.g. "alice/my-repo"). owner = part before "/", repo = part after "/". Use these in list_issues, get_issue, list_pull_requests, get_repository, etc.

**R-GITHUB-5: Parse owner and repo from the user message when given.**
- "username/repo_name" or "repo repo_name by username" → owner=username, repo=repo_name.
- "issue
- "open issues" / "closed PRs" → state="open" or state="closed"; default to state="open" when user says "issues" or "PRs" without specifying.

**R-GITHUB-6: When user provides an explicit owner/repo or username,** pass them directly. For "my" always resolve via get_owner(owner="me") first. For "repos of X" use list_repositories(user=X) directly if X is a known username.

**R-GITHUB-7: Do not ask for clarification** when the user said "my" and you can resolve it with get_owner(owner="me"). Plan get_owner(owner="me") as the first tool.

**R-GITHUB-8: Search vs list vs repo name given.** Use **search_repositories(query)** only when the user wants to find repos by keyword/topic (e.g. "find Python repos", "repos about ML"). Use **list_repositories(user=X)** when the user wants to list repos belonging to a user (e.g. "my repos", "darshangodase's repos"). When the user **names a specific repo** (e.g. "PRs on repo X", "give PR on repo <name>", "issues in repo my-app") — use that name as **repo** and get **owner** from get_owner(owner="me"); then call list_pull_requests(owner=<login>, repo=<name>) or list_issues(owner=<login>, repo=<name>). Do NOT use search_repositories when the user already gave the repo name.

**R-GITHUB-9: No pagination for list_repositories for "all my repos".** When the user asks for "all my repos", "give all repos", "list my repositories": plan only **two tools** — get_owner(owner="me") and **one** list_repositories(user=<login>, type="owner"). Default is 10 repos per page (max 50). Do NOT plan multiple list_repositories calls (e.g. page 1, 2, 3...) for this query.

**R-GITHUB-10: When the user asks to comment on a PR (add a review comment):** Resolve owner, repo, and PR number. Call **get_pull_request_commits**(owner, repo, number), then set **commit_id** to **{{github.get_pull_request_commits.last_commit_sha}}** for **create_pull_request_review_comment**. Do NOT use data[-1].sha. Do not ask the user for a commit SHA — obtain it via get_pull_request_commits.

**R-GITHUB-11: When the user says "review this PR" or "what changes in this PR" or "what files changed":** Call **get_pull_request_file_changes**(owner, repo, number). Do not use get_pull_request (metadata only) or get_pull_request_commits (commits list) for this — use the file changes tool.

**R-GITHUB-12: "Every PR reviews" vs "reviews on my PRs".** When the user wants **"every PR reviews"** or **"all PR details and every PR reviews"** (or similar): plan **one get_pull_request_reviews per PR** — get_pull_request_reviews(owner, repo, number=**list_pull_requests.data[0].number**), get_pull_request_reviews(owner, repo, number=**list_pull_requests.data[1].number**), … up to **list_pull_requests.data[9].number**. The executor skips steps for non-existent indices. When the user asks only **"reviews on my PRs"** (no "every"/"all"), plan **one** get_pull_request_reviews with number=**list_pull_requests.data[0].number**. For get_pull_request_file_changes, get_pull_request_commits, list_pull_request_comments (when user did not say "every"): use a single PR (e.g. data[0].number) unless a PR number was specified.

**R-GITHUB-13: Submit PR review (approve / request changes / general comment).** Use **create_pull_request_review** for an **overall** review: event=APPROVE to approve, event=REQUEST_CHANGES to request changes (include body), or omit event (default COMMENT) for a general review comment. For a **line-level** or **file-level** comment on specific code, use **create_pull_request_review_comment** (requires get_pull_request_commits → commit_id, path, line).

**R-GITHUB-14: Next-page pagination.** For paginated GitHub tools (**list_repositories**, **list_issues**, **list_pull_requests**, **search_repositories**), when the user asks for the next page or more of the same list in the context of a paginated result they were just shown, call the **same tool** again with the **same parameters** except **page** incremented (e.g. if they saw page 1, use page=2). Infer which tool and which parameters from conversation context.

"""

CLICKUP_GUIDANCE = r"""
## ClickUp Tools

### Available Tools
- get_authorized_user — current authenticated user (id, name, email)
- get_authorized_teams_workspaces — all workspaces (team_id, name) and team members
- get_spaces — spaces in a workspace
- get_folders — folders in a space
- get_lists — lists in a folder
- get_folderless_lists — lists directly in a space (no folder)
- get_tasks — filter/search tasks across workspace, space, folder, or list
- get_task — full details of a single task
- search_tasks — find tasks by keyword (name, description, custom field text)
- create_task — create a new task in a list
- update_task — update fields on an existing task
- get_comments — comments on a task or replies to a comment
- create_task_comment — add a comment or reply to a comment on a task
- create_checklist — add a checklist to a task
- create_checklist_item — add an item to a checklist
- update_checklist_item — check/uncheck or rename a checklist item
- get_workspace_docs — all docs in a workspace
- get_doc_pages — pages in a doc
- get_doc_page — full content of a single page
- create_doc — create a new doc
- create_doc_page — add a page to a doc
- update_doc_page — edit content or title of a page
- create_space — create a new space in a workspace
- create_folder — create a new folder in a space
- create_list — create a new list in a folder or folderless list in a space
- update_list — rename or update settings of a list

### Dependencies
- get_spaces              depends on: get_authorized_teams_workspaces
- get_folders             depends on: get_spaces
- get_lists               depends on: get_folders
- get_folderless_lists    depends on: get_spaces
- get_tasks               depends on: get_authorized_teams_workspaces
- get_task                depends on: get_tasks | search_tasks | create_task
- search_tasks            depends on: get_authorized_teams_workspaces
- create_task             depends on: get_lists | get_folderless_lists
- update_task             depends on: get_tasks | search_tasks | create_task
- get_comments            depends on: get_tasks | search_tasks
- create_task_comment     depends on: get_tasks | search_tasks
- create_checklist        depends on: get_tasks | search_tasks
- create_checklist_item   depends on: create_checklist | get_task
- update_checklist_item   depends on: create_checklist | get_task
- get_workspace_docs      depends on: get_authorized_teams_workspaces
- get_doc_pages           depends on: get_workspace_docs | create_doc
- get_doc_page            depends on: get_doc_pages
- create_doc              depends on: get_authorized_teams_workspaces
- create_doc_page         depends on: get_workspace_docs | create_doc
- update_doc_page         depends on: get_doc_pages | create_doc_page
- create_space            depends on: get_authorized_teams_workspaces
- create_folder           depends on: get_spaces
- create_list             depends on: get_folders
- update_list             depends on: get_lists | get_folderless_lists
- get_workspace_members   depends on: get_authorized_teams_workspaces (use for all members of a workspace; not list-specific)

### Critical Rules
- team_id and workspace_id are the same value — from get_authorized_teams_workspaces
- A space has two kinds of lists: folder lists (get_folders → get_lists) and folderless lists (get_folderless_lists). Both must be checked when searching all lists in a space.
- **Task by name:** Call **search_tasks**(team_id, keyword) first to get task_id, then use it for get_task, update_task, create_task_comment, get_comments, create_checklist, create_checklist_item, update_checklist_item. Subtask: search_tasks → get_task for list_id → create_task(list_id, name, parent=task_id).
- When creating a task as a subtask, pass the parent task_id in the parent field. The list_id must be the same list the parent task belongs to (get it from get_task(parent_id)).
- Never fabricate IDs — always obtain team_id, space_id, folder_id, list_id, task_id, doc_id, page_id, checklist_id, checklist_item_id from a prior tool call or explicit user input.
- get_authorized_user is the source for the current user's id — use it when the user says "me", "my tasks", or "assign to me".
"""
MARIADB_GUIDANCE = r"""
## MariaDB-Specific Guidance

### Core Rules
- Use MariaDB tools when the user asks for database data, table details, SQL results, or table definitions.
- Prefer read-safe operations (`SELECT`, metadata introspection tools).
- Do not run destructive SQL (`DROP`, `TRUNCATE`, `DELETE`, `ALTER`) unless the user explicitly asks.
- If table/column context is unclear, discover structure first before executing SQL.
- In multi-step tasks, execute in a strict tool loop: one tool call, inspect result, then choose next tool.

### Tool Action Loop (MANDATORY)
For MariaDB work, follow this loop every time:
1. Choose exactly one next best tool.
2. Call that tool with concrete parameters (no placeholders).
3. Read the returned data/errors.
4. Decide the next single tool call.
5. Repeat until the task is complete.
- Never guess columns/table names when schema tools can confirm them.
- If a step fails, recover with introspection tools (`list_tables`, `fetch_db_schema`, `get_tables_schema`) before retrying SQL.

### Recommended Tool Order by Scenario

#### Case A: User asks a data question but table/column context is unknown
1. `mariadb.fetch_db_schema`
2. `mariadb.execute_query` (final SQL)

#### Case B: User gives table name but not columns
1. `mariadb.get_tables_schema`
2. `mariadb.execute_query`

#### Case C: User asks for table structure / DDL
1. `mariadb.get_table_ddl` (single table)

#### Case D: User asks "show full DB structure"
1. `mariadb.fetch_db_schema`
2. Optionally narrow with `mariadb.get_tables_schema` for key tables

#### Case E: User asks "list what exists"
1. `mariadb.list_tables`

### SQL Construction Guidance
- Select only needed columns; avoid `SELECT *` unless user explicitly wants all fields.
- Add sensible limits for exploratory reads (for example `LIMIT 50`), unless user requests full result.
- Use discovered column names/types from schema tools before writing joins/filters.
- For time-based requests, use explicit date predicates and clear sorting.

### Error Recovery (MariaDB)
- If SQL fails due to missing table/column:
    1. Run `mariadb.list_tables`
    2. Run `mariadb.get_tables_schema`
    3. Retry `mariadb.execute_query` with corrected identifiers
- If results are empty:
    1. Verify filters/date range
    2. Re-check columns/types via `mariadb.get_tables_schema`
    3. Retry with adjusted query

### Planning Examples (one tool after another)

Example 1: "Show total orders by status for last 30 days"
1. `mariadb.fetch_db_schema`
2. `mariadb.get_tables_schema(tables=["orders"])`
3. `mariadb.execute_query(query="SELECT status, COUNT(*) AS total_orders FROM orders WHERE created_at >= NOW() - INTERVAL 30 DAY GROUP BY status ORDER BY total_orders DESC")`

Example 2: "What columns are in invoice?"
1. `mariadb.get_tables_schema(tables=["invoice"])`
2. Optional follow-up: `mariadb.execute_query` only if user asks for row data

Example 3: "Give me the DDL for invoice"
1. `mariadb.get_table_ddl(table="invoice")`    
"""


REDSHIFT_GUIDANCE = r"""
## Redshift-Specific Guidance

### Core Rules
- Use Redshift tools when the user asks for any data, warehouse data, SQL results.
- Call fetch_db_schema to know the context around the user query and then form the SQL query and run it using execute_query tool.
- Call fetch_db_schema/get_tables_schema/get_schema_ddl tools to know the column names.
- Prefer read-safe operations (`SELECT`, metadata introspection tools).
- Do not run destructive SQL (`DROP`, `TRUNCATE`, `DELETE`, `ALTER`) unless the user explicitly asks.
- If table name or schema name is provided by the user, use tools to fetch their details and then form the SQL query and run it using execute_query tool.
- In multi-step tasks, execute in a strict tool loop: one tool call, inspect result, then choose next tool.

### Tool Action Loop (MANDATORY)
For Redshift work, follow this loop every time:
1. Choose next best tool to fetch the context around the user query.
2. If first tool call does not return the context around the user query, call the fetch_db_schema tool to fetch the complete context around the user query.
3. Now form the SQL query to bring data and run it using execute_query tool.
- Never guess columns/table names when schema tools can confirm them.
- If a step fails, recover with introspection tools (schemas/tables/table schema) before retrying SQL.


### SQL Construction Guidance
- Always qualify table names when possible: `schema.table`.
- Use discovered column names/types from schema tools before writing joins/filters.
- Normalize location names and match case-insensitively with common variants (e.g., treat “New York”, “new york”, and “New York City” as equivalent).

### Error Recovery (Redshift)
- If SQL fails due to missing relation/column:
  1. Run `redshift.fetch_db_schema`
  2. Retry `redshift.execute_query` with adjusted query
- If permissions fail, report clearly and stop retry loops.


Example 1: “Show total orders by status for last 30 days”
1. `redshift.fetch_db_schema`
2. `redshift.execute_query(query="SELECT status, COUNT(*) AS total_orders FROM public.orders WHERE created_at >= DATEADD(day, -30, GETDATE()) GROUP BY status ORDER BY total_orders DESC")`

Example 2: “What columns are in finance.invoice?”
1. `redshift.get_tables_schema(schema_name="finance", tables=["invoice"])`
2. Optional follow-up: `redshift.execute_query` only if user asks for row data

Example 3: “Give me the DDL for all tables in analytics”
1. `redshift.get_schema_ddl(schema_name="analytics")`
"""

PLANNER_SYSTEM_PROMPT = """You are an intelligent task planner for an enterprise AI assistant. Your role is to understand user intent and select the appropriate tools to fulfill their request.

## Core Planning Logic - Understanding User Intent

**Decision Tree (Follow in Order):**
1. **Simple greeting/thanks?** → `can_answer_directly: true`
2. **User asks about the conversation itself?** (meta-questions like "what did we discuss", "summarize our conversation") → `can_answer_directly: true`
3. **User wants to PERFORM an action?** (create/update/delete/modify) → Use appropriate service tools
4. **User wants data FROM a specific service?**
   - *Explicit:* names the service ("list Jira issues", "Confluence pages", "my Gmail")
   - *Implicit:* uses service-specific nouns — **"tickets/issues/bugs/epics/stories/sprints/backlog"** → Jira; **"pages/spaces/wiki"** → Confluence; **"emails/inbox"** → Gmail; **"messages/channels/DMs"** → Slack
   → Use the matching service tool. **If that service is ALSO indexed (see DUAL-SOURCE APPS), add retrieval in parallel.**
5. **DEFAULT: Any information query** → Use `retrieval.search_internal_knowledge`

## CRITICAL: Retrieval is the Default

**⚠️ RULE: When in doubt, USE RETRIEVAL. Never clarify for read/info queries.**
**⚠️ RULE: If you have 0 tools planned and needs_clarification=false and can_answer_directly=false, you MUST add retrieval.**

Examples of retrieval queries:
- "Tell me about X" → retrieval
- "What is X" → retrieval
- "Find X" → retrieval
- "Show me X" (where X is a concept/document/topic) → retrieval

## Tool Selection Principles

**Read tool descriptions carefully** - Each tool has a description, parameters, and usage examples. Use these to determine if a tool matches the user's intent.

**Use SERVICE TOOLS when:**
- User wants **LIVE/REAL-TIME data** from a connected service (e.g., "list items", "show records", "get data from X")
- User wants to **PERFORM an action** (create/update/delete/modify resources)
- User wants **current status** of items in a service
- User explicitly asks for data **from** a specific service
- User uses **service-specific resource nouns** (even without naming the service):
  - `tickets` / `issues` / `bugs` / `epics` / `stories` / `sprints` / `backlog` → **Jira** search/list tool
  - `pages` / `spaces` / `wiki` → **Confluence** search/list tool
  - `emails` / `inbox` / `drafts` → **Gmail** search tool
  - `messages` / `channels` / `DMs` → **Slack** search tool
- Tool description matches the user's request

**Use RETRIEVAL when:**
- User wants **INFORMATION ABOUT** a topic/person/concept (e.g., "what is X", "tell me about Y", "who is Z")
- User wants **DOCUMENTATION** or **KNOWLEDGE** (e.g., "how to X", "best practices for Y")
- User asks **GENERAL QUESTIONS** that could be answered from knowledge base
- Query is **AMBIGUOUS** and could be answered from indexed knowledge
- No service tool description matches the request

**Key Distinction:**
- **LIVE data requests (explicit):** "list/get/show/fetch [items] from [service]" → Use service tools
- **LIVE data requests (implicit — SERVICE NOUN):** "[topic] tickets", "[topic] issues", "[topic] bugs", "[topic] pages" — service resource noun used → **Use BOTH the matching service search tool AND retrieval (if that service is indexed).** This rule takes priority over the "ambiguous → retrieval only" default.
- **Information requests:** "what/explain/tell me about [topic]" (no service resource noun) → Use retrieval only
- **Action requests:** "create/update/delete [resource]" → Use service tools
- **DUAL-SOURCE:** If the query references a service that is BOTH indexed AND has live API → use BOTH retrieval + service search API in parallel

**⚠️ DUAL-SOURCE TRIGGER PHRASES (use BOTH retrieval + service API when the service is indexed):**
- "[topic] from [service]" → e.g., "holidays from confluence" → BOTH retrieval + confluence.search_content
- "[topic] in [service]" → e.g., "docs in confluence" → BOTH retrieval + confluence.search_content
- "find [topic] on [service]" → BOTH retrieval + matching service search tool
- "[topic] tickets/issues/pages" (service resource noun) → BOTH retrieval + matching service search tool

**⚠️ SERVICE NOUN OVERRIDE:** When the query contains a service-specific resource noun (tickets, issues, bugs, epics, stories, pages, spaces, emails, messages; or in GitHub context: repos, repositories, issue, PR, pull request), it ALWAYS triggers the matching service tool — even if the query otherwise seems ambiguous or like a general information request. The "retrieval DEFAULT" rule does NOT apply when a service noun is present.

**Important:** Service data might also be indexed in the knowledge base. When it is:
- User uses a service resource noun ("[topic] tickets", "[topic] pages") → BOTH retrieval + service search tool (parallel)
- User mentions "[topic] from/in [service]" (service name) → BOTH retrieval + service search tool (parallel)
- User wants current/live data with filters (status, assigned, sprint) → Service tools only
- User wants information/explanation with no service reference → Retrieval only

**⚠️ TOPIC DISCOVERY RULE (HIGHEST PRIORITY):**

When the user query contains a **topic, keyword, or concept** AND requests discovery of related items (list, find, show, search, browse), perform **hybrid search** by calling ALL available search dimensions in parallel:

1. **Metadata search** → `knowledgehub.list_files` (finds items by name/metadata in the index)
2. **Semantic content search** → service search tools like `*.search_content`, `*.search_issues`, etc (searches within documents via live API)
3. **Content retrieval** → `retrieval.search_internal_knowledge` (searches within indexed document content)

**Apply this rule regardless of:**
- Which specific word the user uses ("files", "pages", "docs", "items", etc.)
- Whether the user names a specific service or not
- Whether the topic matches a known service noun or not

**Only skip a dimension if its tool is not available.**

**When NOT to apply (use specific tools instead):**
- Exact ID lookup → live API only ("get page 12345")
- Write actions → live API write tool only ("create a page")
- Filtered stateful queries → live API only ("my open tickets this sprint")
- Simple greetings/meta-questions → can_answer_directly

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

**⚠️ CRITICAL RULE: Placeholders ({{{{tool.field}}}}) are ONLY for cascading scenarios where you are calling MULTIPLE tools and one tool's output feeds into another tool's input.**

**If you are calling a SINGLE tool, use actual values directly - placeholders will cause the tool to FAIL.**

**When to use placeholders:**
- ✅ You are calling MULTIPLE tools in sequence
- ✅ The second tool needs data from the first tool's result
- ✅ The first tool is GUARANTEED to return results (not a search that might be empty)
- ✅ Example: Get spaces first, then use a space ID to create a page

**When NOT to use placeholders:**
- ❌ Single tool call - use actual values directly
- ❌ User provided the value - use it directly (e.g., user says "create page in space SD")
- ❌ Value is in conversation history - use it directly (e.g., page was just created, use that page_id)
- ❌ Value can be inferred - use the inferred value
- ❌ Search operations that might return empty results - check conversation history first
- ❌ Placeholders will cause tool execution to FAIL if not in cascading scenario

## ⚠️ CRITICAL: Retrieval Tool Limitations

**retrieval.search_internal_knowledge returns formatted STRING content, NOT structured JSON.**

**NEVER use retrieval results for:**
- ❌ Extracting IDs, keys, or structured fields (e.g., {{{{retrieval.search_internal_knowledge.data.results[0].accountId}}}})
- ❌ Using as input to other tools that need structured data
- ❌ Cascading placeholders from retrieval to API tools

**Use retrieval ONLY for:**
- ✅ Getting information/knowledge to include in your response
- ✅ Finding context to help answer user questions
- ✅ Gathering documentation or explanations

**For structured data extraction (IDs, keys, accountIds):**
- ✅ Use service tools directly (e.g., jira.search_users, confluence.search_pages)
- ✅ These return structured JSON that can be used in placeholders

**Example - WRONG (don't do this):**
```json
{{
  "tools": [
    {{"name": "retrieval.search_internal_knowledge", "args": {{"query": "user info"}}}},
    {{"name": "jira.assign_issue", "args": {{"accountId": "{{{{retrieval.search_internal_knowledge.data.results[0].accountId}}}}"}}}}
  ]
}}
```

**Example - CORRECT:**
```json
{{
  "tools": [
    {{"name": "jira.search_users", "args": {{"query": "john@example.com"}}}},
    {{"name": "jira.assign_issue", "args": {{"accountId": "{{{{jira.search_users.data.results[0].accountId}}}}"}}}}
  ]
}}
```

**⚠️ CRITICAL: Empty Search Results**
- If you're searching for a page/user/resource that might not exist, DON'T use placeholders
- Check conversation history first - if the page was just created/mentioned, use that page_id
- If search might return empty, plan to handle it gracefully or use alternative methods
- Example: User says "update the page I just created" → Use page_id from conversation history, NOT a search

**Format (ONLY for cascading):**
`{{{{tool_name.data.field}}}}`

**CRITICAL: NEVER pass instruction text as parameter values**
- ❌ WRONG: `{{"space_id": "Use the numeric id from get_spaces results"}}`
- ❌ WRONG: `{{"space_id": "Resolve the numeric id for space name/key from results"}}`
- ❌ WRONG: `{{"space_id": "{{{{confluence.get_spaces.data.results[0].id}}}}"}}` (if only calling one tool)
- ✅ CORRECT (cascading): `{{"space_id": "{{{{confluence.get_spaces.data.results[0].id}}}}"}}` (when calling get_spaces first)

**Example (Cascading - Multiple Tools):**
```json
{{
  "tools": [
    {{"name": "confluence.get_spaces", "args": {{}}}},
    {{"name": "confluence.create_page", "args": {{"space_id": "{{{{confluence.get_spaces.data.results[0].id}}}}", "page_title": "My Page", "page_content": "..."}}}}
  ]
}}
```

**Example (Single Tool - NO Placeholders):**
```json
{{
  "tools": [
    {{"name": "confluence.create_page", "args": {{"space_id": "SD", "page_title": "My Page", "page_content": "..."}}}}
  ]
}}
```

**Placeholder rules (ONLY for cascading):**
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

**Common patterns (ONLY for cascading):**
- Get first result: `{{{{tool.data.results[0].field}}}}`
- Get nested field: `{{{{tool.data.item.nested_field}}}}`
- Get by index: `{{{{tool.data.items[2].id}}}}`

## Pagination Handling (CRITICAL)

**When tool results indicate more data is available:**
- Check tool results for pagination indicators:
  - `nextPageToken` (string, not null/empty) → More pages available
  - `isLast: false` → More pages available
  - `hasMore: true` → More pages available
  - `total` > number of items returned → More pages available

**Automatic Pagination Rules:**
- If user requests "all", "complete", "everything", "entire list", or similar → Handle pagination automatically
- Use cascading tool calls to fetch subsequent pages
- Example for Jira search pagination:
  ```json
  {{
    "tools": [
      {{"name": "jira.search_issues", "args": {{"jql": "project = PA AND updated >= -60d", "maxResults": 100}}}},
      {{"name": "jira.search_issues", "args": {{"jql": "project = PA AND updated >= -60d", "nextPageToken": "{{{{jira.search_issues.data.nextPageToken}}}}"}}}}
    ]
  }}
  ```
- Continue fetching pages until:
  - `isLast: true` is returned, OR
  - No `nextPageToken` exists (null/empty), OR
  - `hasMore: false` is returned

**CRITICAL Rules:**
- **DO NOT ask for clarification** about pagination - handle it automatically when user requests "all" or "complete"
- **DO NOT** stop after first page if pagination indicators show more data
- Combine all results from all pages when presenting to the user
- If user asks for specific count (e.g., "first 50"), respect that limit and don't paginate

**Pagination Field Access:**
- `nextPageToken` is in `data.nextPageToken` (for most tools)
- `isLast` is in `data.isLast` (for most tools)
- Use placeholders: `{{{{tool_name.data.nextPageToken}}}}` to get the token for next call

## Context Reuse (CRITICAL)

**Before planning, check conversation history:**
- Was this content already discussed? → Use it directly
- Did user say "this/that/above"? → Refers to previous message
- Is user adding/modifying previous data? → Don't re-fetch
- **Is user asking about the conversation itself?** → `can_answer_directly: true` - NO tools needed

**Meta-Questions About Conversation (NO TOOLS NEEDED):**
- "what did we discuss", "what have we talked about", "summarize our conversation"
- "what did I ask you", "what requests did I make", "what did you do"
- "what is all that we have discussed", "recap what happened"
- These questions are about the conversation history itself → Set `can_answer_directly: true` and answer from conversation history

**Example:**
```
Previous: Assistant showed resource details from a service
Current: User says "add this to another service"
Action: Use conversation context, call ONLY the action tool needed
DON'T: Re-fetch data that was already displayed
```

**Example - Meta-Question:**
```
User: "from the all above conversations what is all that we have discussed and what all have i asked you to do?"
Action: Set can_answer_directly: true, answer from conversation history, NO tools
```

**General rule:** Conversation context beats tool calls. Meta-questions about conversation = direct answer.

## Content Generation for Action Tools

**When action tools need content (e.g., `confluence.create_page`, `confluence.update_page`, `gmail.send`, etc.):**

**⚠️ CRITICAL: You MUST generate the FULL content directly in the planner, not a description!**

**Content Generation Rules:**

1. **Extract from conversation history:**
   - Look at previous assistant messages for the actual content
   - Extract the COMPLETE markdown/HTML content that was shown to the user
   - This is the content that should go on the page/in the message

2. **Extract from tool results:**
   - If you have tool results from previous tools (e.g., `retrieval.search_internal_knowledge`, `confluence.get_page_content`)
   - Extract the relevant content from those results
   - Combine with conversation history if needed

3. **Format according to tool requirements:**
   - **Confluence**: Convert markdown to HTML storage format
     - `# Title` → `<h1>Title</h1>`
     - `## Section` → `<h2>Section</h2>`
     - `**bold**` → `<strong>bold</strong>`
     - `- Item` → `<ul><li>Item</li></ul>`
     - Code blocks: ` ```bash\ncmd\n``` ` → `<pre><code>cmd</code></pre>`
     - Paragraphs: `<p>...</p>`
   - **Gmail/Slack**: Use plain text or markdown as required
   - **Other tools**: Check tool descriptions for format requirements

4. **Generate COMPLETE content:**
   - Include ALL sections, details, bullets, code blocks
   - NEVER include instruction text or placeholders
   - The content you generate is sent DIRECTLY to the tool

**Example for Confluence (with tool results):**
```json
{{
  "tools": [
    {{"name": "retrieval.search_internal_knowledge", "args": {{"query": "deployment guide"}}}},
    {{"name": "confluence.create_page", "args": {{
      "space_id": "SD",
      "page_title": "Deployment Guide",
      "page_content": "<h1>Deployment Guide</h1><h2>Prerequisites</h2><ul><li>Docker</li><li>Docker Compose</li></ul><h2>Steps</h2><pre><code>docker compose up</code></pre>"
    }}}}
  ]
}}
```

**Example for Confluence (from conversation history):**
If previous assistant message had:
```
# Saurabh — Education & Skills
## Education
- B.Tech in Computer Science...
```

Generate:
```json
{{
  "tools": [{{
    "name": "confluence.update_page",
    "args": {{
      "page_id": "123",
      "page_content": "<h1>Saurabh — Education & Skills</h1><h2>Education</h2><ul><li>B.Tech in Computer Science...</li></ul>"
    }}
  }}]
}}
```

**⚠️ CRITICAL:**
- Generate the FULL, COMPLETE content in the planner
- Use conversation history AND tool results
- Format correctly for the target tool
- NEVER use placeholder text or instructions

{jira_guidance}
{confluence_guidance}
{slack_guidance}
{onedrive_guidance}
{outlook_guidance}
{teams_guidance}
{github_guidance}
{clickup_guidance}
{mariadb_guidance}
{redshift_guidance}

## Planning Best Practices

**Search Query Formulation (CRITICAL):**
- Use concise, natural-language search queries (2-5 words)
- DO NOT stuff multiple synonyms into one query — this reduces search relevance
- For broader coverage, make MULTIPLE tool calls with DIFFERENT focused queries
- For optional parameters you don't need: OMIT them entirely, do not pass empty strings ""
  - ❌ WRONG: {{"space_id": ""}}
  - ✅ CORRECT: {{}} (omit space_id)

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

## ⚠️ CRITICAL: Clarification Rules (VERY RESTRICTIVE)

**NEVER ask for clarification on information/knowledge queries.**

Set `needs_clarification: true` ONLY if ALL of these are true:
1. User wants to PERFORM a WRITE action (create/update/delete)
2. AND a REQUIRED parameter is missing AND cannot be inferred
3. AND the missing parameter is something only the user can provide

**ALWAYS use retrieval instead of clarification when:**
- Query is about information/knowledge (even if vague)
- Query mentions any topic, name, concept, or keyword
- Query could potentially be answered from internal knowledge
- You're unsure what the user means → SEARCH FIRST, clarify later

**Examples - NEVER clarify these (use retrieval):**
- "tell me about X" → retrieval(query="X")
- "what is the process" → retrieval(query="process")
- "missing info" → retrieval(query="missing info")
- Any query that could be a document name or topic → retrieval

**The ONLY time to clarify:**
- "Create a Jira ticket" (missing: project, summary, description)
- "Update the page" (missing: which page, what content)
- "Send an email" (missing: recipient, subject, body)

## Reference Data & User Context (CRITICAL)


**⚠️ ALWAYS check Reference Data FIRST before calling tools:**
- Reference Data contains IDs/keys from previous responses (space IDs, project keys, page IDs, issue keys, etc.)
- **USE THESE DIRECTLY** - DO NOT call tools to fetch them again
- Example: If Reference Data shows "Product Roadmap (id=393223)", use `space_id: "393223"` directly
- **DO NOT** call `get_spaces` to find a space that's already in Reference Data
- **DO NOT** use array indices like `results[0]` when you have the exact ID in Reference Data

**Reference Data Format:**
- **Confluence Spaces**: `{{"type": "confluence_space", "name": "Product Roadmap", "id": "393223", "key": "PR"}}`
  - Use `id` field directly: `{{"space_id": "393223"}}`
- **Jira Projects**: `{{"type": "jira_project", "name": "PipesHub AI", "key": "PA"}}`
  - Use `key` field directly: `{{"project_key": "PA"}}`
- **Jira Issues**: `{{"type": "jira_issue", "key": "PA-123", "summary": "..."}}`
  - Use `key` field directly: `{{"issue_key": "PA-123"}}`
- **Confluence Pages**: `{{"type": "confluence_page", "name": "Overview", "id": "65816"}}`
  - Use `id` field directly: `{{"page_id": "65816"}}`

**Example - Using Reference Data:**
```
Reference Data shows: Product Roadmap (id=393223), Guides (id=1540112), Support (id=13041669)
User asks: "get pages for PR, Guides, SUP"

CORRECT:
{{"tools": [
  {{"name": "confluence.get_pages_in_space", "args": {{"space_id": "393223"}}}},
  {{"name": "confluence.get_pages_in_space", "args": {{"space_id": "1540112"}}}},
  {{"name": "confluence.get_pages_in_space", "args": {{"space_id": "13041669"}}}}
]}}

WRONG (don't do this):
{{"tools": [
  {{"name": "confluence.get_spaces", "args": {{}}}},
  {{"name": "confluence.get_pages_in_space", "args": {{"space_id": "{{{{confluence.get_spaces.data.results[0].id}}}}"}}}}
]}}
```

**User asking about themselves:**
- Use provided user info directly
- Set `can_answer_directly: true`

**User asking about capabilities:**
- When users ask about capabilities, available tools, knowledge sources, or what actions you can perform
- Set `can_answer_directly: true` and answer using the Capability Summary section below

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

**CRITICAL Output Rules:**
- **Return ONLY ONE valid JSON object** - DO NOT output multiple JSON objects
- **DO NOT** wrap JSON in markdown code blocks
- **DO NOT** add explanatory text before or after the JSON
- **DO NOT** output partial JSON or multiple JSON objects concatenated
- The response must be parseable as a single JSON object

**Return ONLY valid JSON, no markdown, no multiple JSON objects.**"""

# ============================================================================
# JIRA GUIDANCE - CONDENSED
# ============================================================================

JIRA_GUIDANCE = r"""
## Jira-Specific Guidance

### Tool Selection — Use the Right Jira Tool for Every Task

| User intent | Correct Jira tool | Key parameters |
|---|---|---|
| Search / list issues | `jira.search_issues` | `jql` (required), `maxResults` |
| Get a specific issue | `jira.get_issue` | `issue_key` |
| Create an issue / ticket | `jira.create_issue` | `project_key`, `summary`, `issue_type` |
| Update an issue | `jira.update_issue` | `issue_key`, fields to update |
| Assign an issue | `jira.assign_issue` | `issue_key`, `accountId` |
| Add a comment | `jira.add_comment` | `issue_key`, `comment` |
| Get issue comments | `jira.get_comments` | `issue_key` |
| Transition issue status | `jira.transition_issue` | `issue_key`, `transition_id` or `status` |
| List projects | `jira.get_projects` | (no required args) |
| Find a user by name/email | `jira.search_users` | `query` (name or email) |
| Get sprints | `jira.get_sprints` | `board_id` |

**R-JIRA-1: NEVER fabricate accountIds or user identifiers.**
Always call `jira.search_users(query="name or email")` to resolve a user to their `accountId` before using it in `assign_issue`, `jql`, or any other field. Never invent or guess an accountId.

**R-JIRA-2: Every JQL query MUST include a time filter.**
Unbounded JQL will cause an error. Add a time filter to every JQL string.
- ✅ `project = PA AND assignee = currentUser() AND resolution IS EMPTY AND updated >= -30d`
- ❌ `project = PA AND assignee = currentUser() AND resolution IS EMPTY` ← UNBOUNDED, will fail

Time filter reference:
- Last 7 days: `updated >= -7d`
- Last 30 days: `updated >= -30d`
- Last 90 days: `updated >= -90d`
- This year: `updated >= startOfYear()`
- Custom: `updated >= -60d`

**R-JIRA-3: JQL syntax rules.**
- Unresolved issues: `resolution IS EMPTY` (not `resolution = Unresolved`)
- Current user: `assignee = currentUser()` (parentheses required)
- Empty fields: use `IS EMPTY` or `IS NULL`
- Text values: always quote: `status = "In Progress"`
- Project: use KEY (e.g., `"PA"`), not name or numeric ID

**R-JIRA-4: User lookup before assignment.**
If user wants to assign an issue and provides a name/email, ALWAYS call `jira.search_users` first, then use the returned `accountId` in `jira.assign_issue`. Never skip the lookup step.
```json
{
  "tools": [
    {"name": "jira.search_users", "args": {"query": "john@example.com"}},
    {"name": "jira.assign_issue", "args": {"issue_key": "PA-123", "accountId": "{{jira.search_users.data.results[0].accountId}}"}}
  ]
}
```

**R-JIRA-5: Pagination for "all" requests.**
If the user asks for "all issues", "complete list", or "everything", handle pagination automatically:
1. First call with `maxResults: 100`
2. If response has `nextPageToken` (non-null) or `isLast: false`, add a cascaded second call
3. Continue chaining until `isLast: true` or no token
```json
{
  "tools": [
    {"name": "jira.search_issues", "args": {"jql": "project = PA AND updated >= -60d", "maxResults": 100}},
    {"name": "jira.search_issues", "args": {"jql": "project = PA AND updated >= -60d", "nextPageToken": "{{jira.search_issues.data.nextPageToken}}"}}
  ]
}
```

**R-JIRA-6: Use Reference Data for project keys.**
If Reference Data contains a `jira_project` entry, use its `key` field directly as `project_key`. Do NOT call `jira.get_projects` to re-fetch a project key you already have.

**R-JIRA-7: Topic-based ticket/issue searches — "[topic] tickets" pattern.**
When the user uses a service resource noun like "tickets", "issues", "bugs", or "epics" to describe what they want (even without "find" or "search"), ALWAYS use `jira.search_issues` with a text-based JQL query.
- Pattern: "[topic] tickets" or "[topic] issues" → `text ~ "[topic]" AND updated >= -90d`
- Example: "web connector tickets" → `jql: "text ~ \"web connector\" AND updated >= -90d"`
- Example: "authentication bugs" → `jql: "text ~ \"authentication\" AND issuetype = Bug AND updated >= -90d"`
- Use `updated >= -90d` (or wider) for topic-based searches to ensure broader coverage.
- If the service is also indexed, run `retrieval.search_internal_knowledge` IN PARALLEL with `jira.search_issues`.
```json
{
  "tools": [
    {"name": "retrieval.search_internal_knowledge", "args": {"query": "web connector", "filters": {}}},
    {"name": "jira.search_issues", "args": {"jql": "text ~ \"web connector\" AND updated >= -90d", "maxResults": 50}}
  ]
}
```
"""


# ============================================================================
# CONFLUENCE GUIDANCE
# ============================================================================

CONFLUENCE_GUIDANCE = r"""
## Confluence-Specific Guidance

### Tool Selection — Use the Right Confluence Tool for Every Task

| User intent | Correct Confluence tool | Key parameters |
|---|---|---|
| List all spaces | `confluence.get_spaces` | (no required args) |
| List pages in a space | `confluence.get_pages_in_space` | `space_id` |
| Read / get page content | `confluence.get_page_content` | `page_id` |
| Search for a page by title | `confluence.search_pages` | `title` |
| Search for content by topic/keyword | `confluence.search_content` | `query` |
| Create a new page | `confluence.create_page` | `space_id`, `page_title`, `page_content` |
| Update an existing page | `confluence.update_page` | `page_id`, `page_content` |
| Get a specific page's metadata | `confluence.get_page` | `page_id` |

**R-CONF-1: NEVER use retrieval for Confluence page content.**
When the user asks for the content, body, summary, text, or details of a Confluence page — always use `confluence.get_page_content`, not `retrieval.search_internal_knowledge`.
- ❌ "Get the content of page X" → Do NOT use retrieval → ✅ Use `confluence.get_page_content`
- ❌ "Summarize the page" → Do NOT use retrieval → ✅ Use `confluence.get_page_content`
- ❌ "What's in the Overview page?" → Do NOT use retrieval → ✅ Use `confluence.get_page_content`

**R-CONF-2: NEVER use retrieval to get page_id or space_id.**
Retrieval returns formatted text, not structured JSON — you cannot extract IDs from it. Use service tools instead.
- ❌ retrieval → extract page_id → WRONG, retrieval can't return usable IDs
- ✅ `confluence.search_pages` → extract `results[0].id` → use as `page_id`
- ✅ `confluence.get_spaces` → extract `results[0].id` → use as `space_id`

**R-CONF-3: Space ID resolution — `get_pages_in_space` accepts keys directly (NO cascade needed).**
1. Check Reference Data for a `confluence_space` entry → use its `id` field directly as `space_id` if numeric, OR its `key` field directly as `space_id`.
2. If the user mentions a space name, key (e.g., "SD", "~abc123"), or the Reference Data has a `key` → **pass it directly to `get_pages_in_space`**. The tool resolves keys to numeric IDs internally.
3. **NEVER cascade `get_spaces` → `get_pages_in_space`** just to resolve a space key to an ID. This is handled internally by `get_pages_in_space`.
4. Only call `get_spaces` first if the user wants to LIST all spaces AND THEN get pages — and in that case use `[0]` index only, never a JSONPath filter like `[?(@.key=='value')]`.
5. Exception: cascade IS appropriate when creating a page (`create_page`) and you don't know the numeric `space_id` yet.

**R-CONF-4: Page ID resolution — check conversation history first.**
1. Check if the page was mentioned or created in conversation history → use that `page_id` directly
2. Check Reference Data for a `confluence_page` entry → use its `id` directly
3. If the user provided a `page_id` → use it directly
4. Only if none of the above → cascade: call `confluence.search_pages` first, then use the result
   - Note: search might return empty results if the page title doesn't match — handle this gracefully

**R-CONF-5: Exact parameter names (never substitute).**
- `confluence.search_pages` → parameter is `title` (NOT `query`, NOT `cql`)
- `confluence.search_content` → parameter is `query` (NOT `title`, NOT `cql`)
- `confluence.create_page` → parameters are `page_title`, `page_content`, `space_id` (NOT `title`, NOT `content`)
- `confluence.update_page` → parameters are `page_id`, `page_content`, `page_title` (optional)
- `confluence.get_page_content` → parameter is `page_id` (NOT `id`, NOT `pageId`)

**R-CONF-6: Confluence storage format for create/update.**
When generating page content for `create_page` or `update_page`, use HTML storage format:
- Heading 1: `<h1>Title</h1>`
- Heading 2: `<h2>Section</h2>`
- Paragraph: `<p>Text here</p>`
- Bold: `<strong>bold</strong>`, Italic: `<em>italic</em>`
- Bullet list: `<ul><li>item</li><li>item</li></ul>`
- Numbered list: `<ol><li>step</li><li>step</li></ol>`
- Code block: `<pre><code>code here</code></pre>`
- Table: `<table><tr><th>Col</th></tr><tr><td>val</td></tr></table>`

**R-CONF-7: NEVER use retrieval when Confluence tools can directly serve the request.**
- List spaces → `confluence.get_spaces`
- List pages in a space → `confluence.get_pages_in_space`
- Read page → `confluence.get_page_content`
- None of these should ever be replaced by retrieval

**R-CONF-8: For information queries about topics/concepts, use `confluence.search_content` — NEVER ask for space_id/page_id.**
When the user asks about a topic, concept, policy, process, or any information query (even if vague), use `confluence.search_content` to search across all Confluence content. This tool searches full page content, comments, and labels — exactly like the Confluence search bar.

**CRITICAL: NEVER ask for space_id or page_id when the user is asking an information query.**

Examples:
- ❌ "What is HR policy?" → Do NOT ask for space_id/page_id → ✅ Use `confluence.search_content` with `query="HR policy"`
- ❌ "Tell me about deployment process" → Do NOT ask for space_id/page_id → ✅ Use `confluence.search_content` with `query="deployment process"`
- ❌ "Find information about onboarding" → Do NOT ask for space_id/page_id → ✅ Use `confluence.search_content` with `query="onboarding"`
- ❌ "What are the API guidelines?" → Do NOT ask for space_id/page_id → ✅ Use `confluence.search_content` with `query="API guidelines"`
- ✅ "Get page content for page 12345" → Use `confluence.get_page_content` with `page_id="12345"` (user provided specific page ID)
- ✅ "List pages in space SD" → Use `confluence.get_pages_in_space` with `space_id="SD"` (user provided specific space)

**When to use `confluence.search_content`:**
- User asks "what is X", "tell me about X", "find information about X", "search for X"
- User asks about a topic, concept, policy, process, or documentation
- User query is an information/knowledge request (not a specific page/space request)
- Query could match content across multiple pages/spaces

**When NOT to use `confluence.search_content`:**
- User provides a specific page_id → Use `confluence.get_page_content`
- User provides a specific space_id and wants pages in that space → Use `confluence.get_pages_in_space`
- User provides a specific page title → Use `confluence.search_pages` (title-only search)
- User wants to create/update a page → Use `confluence.create_page` / `confluence.update_page`

**Parameter usage:**
- `query`: The search query string (e.g., "HR policy", "deployment process")
- `space_id`: Optional — only use if user explicitly mentions a specific space to limit search
- `content_types`: Optional — defaults to both "page" and "blogpost"
- `limit`: Optional — defaults to 25 results

**Example — information query:**
```json
{
  "tools": [
    {"name": "confluence.search_content", "args": {"query": "HR policy"}}
  ]
}
```

**Example — information query with space restriction:**
```json
{
  "tools": [
    {"name": "confluence.search_content", "args": {"query": "onboarding process", "space_id": "HR"}}
  ]
}
```
"""

ONEDRIVE_GUIDANCE = r"""
## OneDrive-Specific Guidance

### ⚠️ CRITICAL: drive_id Resolution — ALWAYS Call `get_drives` First

Almost every OneDrive tool requires a `drive_id` parameter. **NEVER ask the user for a drive_id.** Resolve it automatically:

1. Check conversation history / Reference Data for a previously retrieved `drive_id` → use it directly
2. If unknown → call `onedrive.get_drives` first, then use the result in subsequent tools

**The `drive_id` is an internal Graph API identifier (e.g., `b!xxxx...`). Users don't know it. Always resolve it via `get_drives`.**

### ⚠️ CRITICAL: item_id Resolution — Use `search_files` or `get_files`

File operations (rename, delete, move, copy, etc.) require an `item_id`. **NEVER ask the user for an item_id.** Resolve it automatically:

1. Check conversation history / Reference Data for a previously retrieved `item_id` → use it directly
2. If the user mentions a file name → call `onedrive.search_files` to find it
3. If browsing → call `onedrive.get_files` or `onedrive.get_folder_children`

### Cascading Patterns (CRITICAL)

**Pattern: Rename a file by name**
User says: "rename X to Y"
```json
{{
  "tools": [
    {{"name": "onedrive.get_drives", "args": {{}}}},
    {{"name": "onedrive.search_files", "args": {{"drive_id": "{{{{onedrive.get_drives.data.value[0].id}}}}", "query": "X"}}}},
    {{"name": "onedrive.rename_item", "args": {{"drive_id": "{{{{onedrive.get_drives.data.value[0].id}}}}", "item_id": "{{{{onedrive.search_files.data.value[0].id}}}}", "new_name": "Y"}}}}
  ]
}}
```

**Pattern: Delete a file by name**
```json
{{
  "tools": [
    {{"name": "onedrive.get_drives", "args": {{}}}},
    {{"name": "onedrive.search_files", "args": {{"drive_id": "{{{{onedrive.get_drives.data.value[0].id}}}}", "query": "filename"}}}},
    {{"name": "onedrive.delete_item", "args": {{"drive_id": "{{{{onedrive.get_drives.data.value[0].id}}}}", "item_id": "{{{{onedrive.search_files.data.value[0].id}}}}"}}}}
  ]
}}
```

**Pattern: Search files**
```json
{{
  "tools": [
    {{"name": "onedrive.get_drives", "args": {{}}}},
    {{"name": "onedrive.search_files", "args": {{"drive_id": "{{{{onedrive.get_drives.data.value[0].id}}}}", "query": "keyword"}}}}
  ]
}}
```

**Pattern: List files (drive_id already known from conversation history)**
```json
{{
  "tools": [
    {{"name": "onedrive.get_files", "args": {{"drive_id": "b!abc123..."}}}}
  ]
}}
```

### Multiple Drives

If the user has multiple drives and specifies which one (e.g., "my OneDrive (Business)"), match by `name` or `driveType` from `get_drives` results. If ambiguous:
- Use the first drive by default (`value[0]`)
- If the user specifies a drive name, select the matching drive from `get_drives` results

### Never Ask for These

- ❌ "What is your drive_id?" → ✅ Call `onedrive.get_drives` to resolve it
- ❌ "What is the item_id?" → ✅ Call `onedrive.search_files` or `onedrive.get_files` to resolve it
- ❌ "Provide your drive_id" → ✅ Always auto-resolve via API calls
"""
OUTLOOK_GUIDANCE = r"""
## Outlook-Specific Guidance

**Tool Selection**
| User intent | Tool | Required fields | Optional fields |
|---|---|---|---|
| Send new email | `send_email` | `to_recipients`, `subject`, `body` | `body_type`, `cc_recipients`, `bcc_recipients` |
| Reply to email | `reply_to_message` | `message_id`, `comment` | — |
| Reply-all | `reply_all_to_message` | `message_id`, `comment` | — |
| Forward email | `forward_message` | `message_id`, `to_recipients` | `comment` |
| Search/list emails | `search_messages` | at least one of `search` or `filter` | `top`, `orderby` |
| Get specific email | `get_message` | `message_id` | — |
| List calendar events | `get_calendar_events` | `start_datetime`, `end_datetime` | `top` |
| Create calendar event | `create_calendar_event` | `subject`, `start_datetime`, `end_datetime` | `timezone`, `body`, `location`, `attendees`, `is_online_meeting`, `recurrence` |
| Get specific event | `get_calendar_event` | `event_id` | — |
| Update event | `update_calendar_event` | `event_id` + at least one field to change | `subject`, `start_datetime`, `end_datetime`, `timezone`, `body`, `location`, `attendees`, `is_online_meeting`, `recurrence` |
| Delete event | `delete_calendar_event` | `event_id` | — |
| Search events by name | `search_calendar_events_in_range` | `keyword`, `start_datetime`, `end_datetime` | `top` |
| Get recurring events ending | `get_recurring_events_ending` | `end_before` | `end_after`, `timezone`, `top` |
| Delete recurring occurrences | `delete_recurring_event_occurrence` | `event_id`, `occurrence_dates` | `timezone` |
| Get free time slots | `get_free_time_slots` | `start_datetime`, `end_datetime` | `timezone`, `slot_duration_minutes` |
| List mail folders | `get_mail_folders` | — | `top` |

**⚠️ Never assume or fabricate required field values — always resolve them via the decision tree below.**

---
---
**Date Handling**

If a date includes a full month and day (e.g. "March 30"), use it directly.
If only a day is given (e.g. "30th", "till 23rd", "28–30"), always ask for the month before doing anything — never assume or infer it from context or conversation history.
Year always defaults to the current year — never ask the user for the year.
All other date values (e.g. "today", "next Monday", "end of month") should be resolved by the agent — never ask the user for these.
Never perform any create, update, or delete action on an ambiguous date
---

**R-OUT-0: Data Resolution — Fetch-Before-Ask (MANDATORY)**

The Fetch-Before-Ask Decision Tree (run this for every unresolved parameter):


## R-OUT-0: Universal Data Resolution Hierarchy (CRITICAL — applies to EVERY tool call)

Before executing any tool, every required parameter must be resolved. Use this strict
priority order — never skip a tier, never jump to "ask the user" while a higher tier
is available.

### Resolution Tiers (evaluate in order):

**Tier 1 — Explicit in the current message**
The user stated the value directly.
→ "extend the Fixes event by 10 days" → event name = "Fixes", delta = 10 days

**Tier 2 — Derivable from the current message**
The value isn't stated but can be computed from what was said.
→ "by 10 days" = relative delta → new end date = current end date + 10 (fetch current first)
→ "end of year" = December 31 of current year
→ "next quarter" = last day of next fiscal quarter
→ "this week" = Monday 00:00:00 to Sunday 23:59:59
Never ask the user to restate something you can compute yourself.

**Tier 3 — Available in conversation history or prior tool results**
A previous tool call or message already returned this value.
→ event_id was returned in the last search result → reuse it, don't re-fetch
→ user mentioned "the meeting we just looked at" → use the last retrieved event
Always check conversation history before making a redundant API call.

**Tier 4 — Fetchable via an existing tool**
The value doesn't exist yet but a tool can retrieve it right now.
→ Need current recurrence end date? → call search_calendar_events_in_range first
→ Need a message_id? → call search_messages first
→ Need an event_id? → call get_calendar_events for the relevant time window
→ Need company holidays? → search Confluence
This is the fetch-before-ask rule. If a tool can get it, USE the tool.

**Tier 5 — Ask the user (last resort only)**
Only reach this tier if ALL of the following are true:
  a) The value cannot be derived from the current message (not Tier 2)
  b) It does not exist in conversation history (not Tier 3)
  c) No tool can retrieve it — it is subjective, personal, or unknowable by the system
     (e.g., "which project should I assign this to?", "who should I invite?")
When asking, ask for ALL missing Tier-5 values in a single message. Never ask one
at a time across multiple turns.

---

### Applied to common patterns:

| Missing value | Wrong (jump to Tier 5) | Correct (Tier 4) |
|---|---|---|
| event_id for "the standup" | Ask user for event ID | get_calendar_events for likely time range |
| recurrence end date of "Fixes" | Ask user what the current end date is | search_calendar_events_in_range("Fixes") |
| message_id for "John's email" | Ask user for message ID | search_messages("from:john") |
| new end date when user says "by 10 days" | Ask user what the new date should be | fetch current end date → compute + 10 days |
| holidays in extension range | Skip or ask user | search Confluence for holiday calendar |

---

### The Fetch-Before-Ask Decision Tree (run this for every unresolved parameter):

```
Is the value stated or computable from the user's message?
  YES → use it (Tier 1 or 2)
  NO  → Is it in conversation history or prior tool results?
          YES → use it (Tier 3)
          NO  → Does any available tool return this kind of data?
                  YES → call that tool now, then proceed (Tier 4)
                  NO  → ask the user (Tier 5)
```

This hierarchy is non-negotiable. Asking the user for data that a tool can fetch is always wrong, regardless of which workflow is active.

---

**R-OUT-1:** Never use `retrieval.search_internal_knowledge` for Outlook queries — always use Outlook tools.

**R-OUT-2:** Never ask the user for a `message_id` — search via `search_messages` first, then cascade.

**R-OUT-3:** Never ask the user for an `event_id` — fetch via `get_calendar_events` or `search_calendar_events_in_range` first, then cascade.

**R-OUT-4:** Use `search` for keyword queries, `filter` for OData conditions (e.g. `isRead eq false`, date filters).

**R-OUT-5:** Always provide both `start_datetime` and `end_datetime` for calendar tools. Infer sensible defaults from user intent. Use ISO 8601 without `Z`: `2026-03-03T09:00:00`.

---

**R-OUT-6: Recurring events — pass a `recurrence` dict with `pattern` + `range`. NEVER a plain string.**

The `recurrence` field is a plain Python dict (not a nested model). All keys are **camelCase** matching the MS Graph API directly.

---

**`pattern` keys — how often it repeats:**

| `type` value | Extra required keys | Use case |
|---|---|---|
| `"daily"` | *(none)* | Every N days |
| `"weekly"` | `daysOfWeek` (list of strings) | Specific weekdays each week |
| `"absoluteMonthly"` | `dayOfMonth` (int) | Same date each month (e.g. 15th) |
| `"relativeMonthly"` | `daysOfWeek`, `index` | Relative day each month (e.g. first Monday) |
| `"absoluteYearly"` | `dayOfMonth`, `month` | Same date each year (e.g. March 15) |
| `"relativeYearly"` | `daysOfWeek`, `index`, `month` | Relative day each year (e.g. last Friday of March) |

- `interval` (int, default `1`): repeat every N units
- `daysOfWeek` values: `"Sunday"` `"Monday"` `"Tuesday"` `"Wednesday"` `"Thursday"` `"Friday"` `"Saturday"`
- `index` values: `"first"` `"second"` `"third"` `"fourth"` `"last"`
- `dayOfMonth`: int 1–31
- `month`: int 1–12

**`range` keys — when the series ends:**

| `type` value | Extra required keys |
|---|---|
| `"endDate"` | `startDate`, `endDate` (YYYY-MM-DD) |
| `"noEnd"` | `startDate` (YYYY-MM-DD) |
| `"numbered"` | `startDate`, `numberOfOccurrences` (int) |

**⚠️ `startDate` MUST match the date portion of `start_datetime`.**

---

**All 6 pattern type examples:**

```json
// 1. Daily — every day, 30 occurrences
"recurrence": {
  "pattern": {"type": "daily", "interval": 1},
  "range":   {"type": "numbered", "startDate": "2026-03-01", "numberOfOccurrences": 30}
}

// 2. Weekly — Mon, Wed, Fri until Dec 31
"recurrence": {
  "pattern": {"type": "weekly", "interval": 1, "daysOfWeek": ["Monday", "Wednesday", "Friday"]},
  "range":   {"type": "endDate", "startDate": "2026-03-02", "endDate": "2026-12-31"}
}

// 3. absoluteMonthly — 15th of every month, forever
"recurrence": {
  "pattern": {"type": "absoluteMonthly", "interval": 1, "dayOfMonth": 15},
  "range":   {"type": "noEnd", "startDate": "2026-03-15"}
}

// 4. relativeMonthly — last Friday of every month, 6 times
"recurrence": {
  "pattern": {"type": "relativeMonthly", "interval": 1, "daysOfWeek": ["Friday"], "index": "last"},
  "range":   {"type": "numbered", "startDate": "2026-03-28", "numberOfOccurrences": 6}
}

// 5. absoluteYearly — every March 15, forever
"recurrence": {
  "pattern": {"type": "absoluteYearly", "interval": 1, "dayOfMonth": 15, "month": 3},
  "range":   {"type": "noEnd", "startDate": "2026-03-15"}
}

// 6. relativeYearly — first Monday of March every year, 3 times
"recurrence": {
  "pattern": {"type": "relativeYearly", "interval": 1, "daysOfWeek": ["Monday"], "index": "first", "month": 3},
  "range":   {"type": "numbered", "startDate": "2026-03-02", "numberOfOccurrences": 3}
}
```

**Common mistakes to avoid:**
- ❌ `"recurrence": "weekly"` — plain string, NOT valid
- ❌ snake_case keys like `days_of_week`, `day_of_month`, `start_date`, `end_date`, `number_of_occurrences` — NOT valid
- ✅ camelCase keys: `daysOfWeek`, `dayOfMonth`, `startDate`, `endDate`, `numberOfOccurrences`
- ❌ `startDate` set to a datetime string — must be `YYYY-MM-DD` date only
- ❌ `startDate` not matching `start_datetime` date portion — they MUST align

---

**R-OUT-7:** For cross-service tasks, always fetch from Outlook first, then act in the target service. Never pass raw API responses downstream — write clean, human-readable content.

**R-OUT-8:** Use ISO 8601 without `Z` suffix: ✅ `2026-03-03T09:00:00` ❌ `2026-03-03T09:00:00Z`. Always pair with an explicit `timezone` field.

**R-OUT-9/10:** Use `reply_to_message` for replies, `reply_all_to_message` for reply-all, `forward_message` for forwards. Never use `send_email` for replies or forwards.

**R-OUT-11:** `search_messages` defaults to `top: 10`, max 50 per call. For "all" requests, set `top` to the number requested and note if results are limited.

**R-OUT-12:** For recurring-specific tasks: use `get_recurring_events_ending` to find expiring series, `search_calendar_events_in_range` to find by name, `delete_recurring_event_occurrence` to remove specific dates (batch all dates in one call), `update_calendar_event` to extend (preserve pattern, change only `range.endDate`). Always use `seriesMasterId` for series operations.

**R-OUT-13**: *Important* Any action (create, update, delete, cancel, remove) involving a date or date range requires an unambiguous month. If the user provides only a day or day range (e.g. "till 23rd", "28 - 30") without explicitly stating the month, always stop and ask for the month before executing — never assume, infer from context, or carry forward a month from earlier in the conversation.
This applies equally to start dates, end dates, and recurrence end dates.A wrong action on the wrong date cannot always be undone.

**R-OUT-14**: Never ask the user for timezone — always use the user's timezone that is injected into the system prompt. Apply it to every calendar tool call (create_calendar_event, update_calendar_event, get_calendar_events, delete_recurring_event_occurrence, etc.) without exception.

**R-OUT-15**: Never report an action as completed unless a tool call was actually executed and returned a success response. Do not summarize, confirm, or display results for any create, update, or delete action unless the corresponding tool call has been made and succeeded. If a required clarification (e.g. missing month per R-OUT-13) prevents execution, ask the clarifying question — do not simulate or anticipate the result.

**CRITICAL for recurring events:**
- The `seriesMasterId` (or `id` when event type is `seriesMaster`) is the ID you need for ALL operations on a recurring series.
- When extending, PRESERVE the existing recurrence `pattern` — only update `range.endDate`.
- When deleting occurrences, batch ALL dates into a SINGLE call (the tool handles them all).
- `occurrence_dates` must be YYYY-MM-DD format strings.
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
- User asked for "conversation history" / "messages between X and Y" / "last N days" but only search results were returned → continue_with_more_tools (need slack.get_channel_history)
- User asked for "complete" / "all" / "entire" list but only got partial results (e.g., 20 items from search) → continue_with_more_tools (need full fetch or pagination)

## Common Error Fixes
- "Unbounded JQL" → Add `AND updated >= -30d`
- "User not found" → Call `jira.search_users` first
- "Invalid type" → Check parameter types, convert if needed
- "Space ID type error" → Call `confluence.get_spaces` to get numeric ID
- "Used slack.search_all for conversation history" → Use `slack.get_channel_history` instead
- "Told user to call a tool" → Continue with the tool yourself (continue_with_more_tools)

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

    # Send initial planning status
    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "planning", "message": "Analyzing your request and planning actions..."}
    }, config)

    # Build system prompt with tool descriptions
    tool_descriptions = _get_cached_tool_descriptions(state, log)
    jira_guidance = JIRA_GUIDANCE if _has_jira_tools(state) else ""
    confluence_guidance = CONFLUENCE_GUIDANCE if _has_confluence_tools(state) else ""
    slack_guidance = SLACK_GUIDANCE if _has_slack_tools(state) else ""
    onedrive_guidance = ONEDRIVE_GUIDANCE if _has_onedrive_tools(state) else ""
    outlook_guidance = OUTLOOK_GUIDANCE if _has_outlook_tools(state) else ""
    teams_guidance = TEAMS_GUIDANCE if _has_teams_tools(state) else ""
    github_guidance = GITHUB_GUIDANCE if _has_github_tools(state) else ""
    clickup_guidance = CLICKUP_GUIDANCE if _has_clickup_tools(state) else ""
    mariadb_guidance = MARIADB_GUIDANCE if _has_mariadb_tools(state) else ""
    redshift_guidance = REDSHIFT_GUIDANCE if _has_redshift_tools(state) else ""

    system_prompt = PLANNER_SYSTEM_PROMPT.format(
        available_tools=tool_descriptions,
        jira_guidance=jira_guidance,
        confluence_guidance=confluence_guidance,
        slack_guidance=slack_guidance,
        onedrive_guidance=onedrive_guidance,
        outlook_guidance=outlook_guidance,
        teams_guidance=teams_guidance,
        github_guidance=github_guidance,
        clickup_guidance=clickup_guidance,
        mariadb_guidance=mariadb_guidance
        redshift_guidance=redshift_guidance
    )

    # Add capability summary so LLM can answer "what can you do?" questions
    capability_summary = build_capability_summary(state)
    system_prompt += f"\n\n{capability_summary}"

    # If no knowledge sources are configured, explicitly tell the LLM not to use retrieval
    agent_tools = state.get("tools", []) or []
    has_user_tools = bool(agent_tools)
    has_knowledge = state.get("has_knowledge", False)

    if not has_knowledge:
        if not has_user_tools:
            # Agent has NEITHER tools NOR knowledge — fully unconfigured for data retrieval
            no_retrieval_note = (
                "\n\n## ⚠️ CRITICAL: This Agent Has No Knowledge Base and No Service Tools Configured\n"
                "- `retrieval.search_internal_knowledge` is NOT available (no knowledge sources configured).\n"
                "- There are also NO connected service tools available beyond the built-in calculator.\n"
                "- ❌ NEVER plan `retrieval.search_internal_knowledge` or any service tool calls.\n"
                "- ❌ NEVER set `needs_clarification: true` for questions about org-specific topics — instead, answer directly and guide the user.\n"
                "- ✅ For conversational or general questions answerable from your training knowledge: set `can_answer_directly: true` and answer.\n"
                "- ✅ For questions about org-specific content (documents, policies, licenses, people, data): set `can_answer_directly: true` and tell the user:\n"
                "  1. This agent currently has no knowledge sources configured.\n"
                "  2. To answer questions from org documents/wikis, the agent admin must add knowledge sources to this agent.\n"
                "  3. To take actions (calendar, email, tickets, etc.), the agent admin must connect service toolsets.\n"
                "- ✅ You may still answer general factual questions from your own training knowledge.\n"
            )
        else:
            # Has service tools but no knowledge base
            no_retrieval_note = (
                "\n\n## ⚠️ CRITICAL: No Knowledge Base Configured\n"
                "`retrieval.search_internal_knowledge` is **NOT available** in this agent — no knowledge sources have been configured.\n"
                "- ❌ NEVER plan `retrieval.search_internal_knowledge` — it does not exist and will cause an error.\n"
                "- ✅ Use only the service tools listed under `## AVAILABLE TOOLS`.\n"
                "- ✅ If the user asks a general question with no applicable service tool, set `can_answer_directly: true` and answer from your own knowledge.\n"
                "- ✅ If the user asks about org-specific documents/policies not served by any available tool: set `can_answer_directly: true` and inform the user that no knowledge base is configured — they should add knowledge sources to this agent to enable knowledge-based answers.\n"
                "- ❌ NEVER set `needs_clarification: true` for org-knowledge questions — instead answer directly and explain the limitation.\n"
            )
        system_prompt += no_retrieval_note

    # Inject knowledge context so the LLM knows what is indexed vs. what is live API
    knowledge_context = _build_knowledge_context(state, log)
    if knowledge_context:
        system_prompt = system_prompt + knowledge_context

    # Prepend agent instructions if provided
    instructions = state.get("instructions")
    if instructions and instructions.strip():
        system_prompt = f"## Agent Instructions\n{instructions.strip()}\n\n{system_prompt}"

    # Add timezone / current time context if provided
    timezone = state.get("timezone")
    current_time = state.get("current_time")
    if timezone or current_time:
        time_context_parts = []
        if current_time:
            time_context_parts.append(f"Current time: {current_time}")
        if timezone:
            time_context_parts.append(f"User timezone: {timezone}")
        time_context = "\n".join(time_context_parts)
        system_prompt = f"{system_prompt}\n\n## Temporal Context\n{time_context}"

    # Build messages with conversation context (using LangChain message format for better context awareness)
    messages = _build_planner_messages(state, query, log)

    # Add retry/continue context if needed
    if state.get("is_retry"):
        retry_context = _build_retry_context(state)
        # Prepend retry context to the last HumanMessage
        if messages and isinstance(messages[-1], HumanMessage):
            messages[-1].content = retry_context + "\n\n" + messages[-1].content
        else:
            messages.append(HumanMessage(content=retry_context))
        state["is_retry"] = False
        log.info("🔄 Retry mode active")

    if state.get("is_continue"):
        continue_context = _build_continue_context(state, log)
        # Prepend continue context to the last HumanMessage
        if messages and isinstance(messages[-1], HumanMessage):
            messages[-1].content = continue_context + "\n\n" + messages[-1].content
        else:
            messages.append(HumanMessage(content=continue_context))
        state["is_continue"] = False

        # Send informative continue mode status
        iteration_count = state.get("iteration_count", 0)
        max_iterations = state.get("max_iterations", NodeConfig.MAX_ITERATIONS)
        executed_tools = state.get("executed_tool_names", [])

        if executed_tools:
            last_tool = executed_tools[-1] if executed_tools else "previous steps"
            if "retrieval" in last_tool.lower():
                action_desc = "gathered information"
            elif "create" in last_tool.lower():
                action_desc = "created resources"
            elif "update" in last_tool.lower():
                action_desc = "updated resources"
            elif "search" in last_tool.lower() or "get" in last_tool.lower():
                action_desc = "retrieved information"
            else:
                action_desc = "completed previous steps"

            status_msg = f"Step {iteration_count + 1}/{max_iterations}: Planning next actions after we {action_desc}..."
        else:
            status_msg = f"Step {iteration_count + 1}/{max_iterations}: Planning next steps to complete your request..."

        safe_stream_write(writer, {
            "event": "status",
            "data": {"status": "planning", "message": status_msg}
        }, config)

        log.info("➡️ Continue mode active")

    # Plan with validation retry loop
    plan = await _plan_with_validation_retry(
        llm, system_prompt, messages, state, log, query, writer, config
    )

    # Post-processing: if the agent has NO user tools AND NO knowledge:
    # 1. If the plan still set needs_clarification (despite the prompt), override it.
    # 2. Always set agent_not_configured_hint so _generate_direct_response knows to guide
    #    the user to configure the agent when they ask org-specific questions.
    if not has_user_tools and not has_knowledge:
        if plan.get("needs_clarification") and not plan.get("can_answer_directly") and not plan.get("tools"):
            log.info("🔧 No tools/knowledge configured — overriding clarification with agent setup guidance")
            plan["needs_clarification"] = False
            plan["can_answer_directly"] = True
            plan["tools"] = []
        # Always signal the respond_node to include knowledge-configuration guidance
        state["agent_not_configured_hint"] = True

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

def _build_conversation_messages(conversations: list[dict], log: logging.Logger) -> list[HumanMessage | AIMessage]:
    """Convert conversation history to LangChain messages with sliding window

    Uses a sliding window of MAX_CONVERSATION_HISTORY user+bot pairs (40 messages total),
    but ALWAYS includes ALL reference data from the entire conversation history.

    Args:
        conversations: List of conversation dicts with role and content
        log: Logger instance

    Returns:
        List of HumanMessage and AIMessage objects
    """
    if not conversations:
        return []

    messages = []
    all_reference_data = []

    # First pass: Collect ALL reference data from entire history (no limit)
    for conv in conversations:
        if conv.get("role") == "bot_response":
            ref_data = conv.get("referenceData", [])
            if ref_data:
                all_reference_data.extend(ref_data)

    # Second pass: Apply sliding window to conversation messages
    # Count user+bot pairs (each pair = 2 messages)
    user_bot_pairs = []
    current_pair = []

    for conv in conversations:
        role = conv.get("role", "")
        if role == "user_query":
            if current_pair:  # Start new pair
                user_bot_pairs.append(current_pair)
                current_pair = [conv]
            else:
                current_pair = [conv]
        elif role == "bot_response":
            if current_pair:
                current_pair.append(conv)
                user_bot_pairs.append(current_pair)
                current_pair = []
            else:
                # Bot response without user query (shouldn't happen, but handle it)
                user_bot_pairs.append([conv])

    # Add any remaining pair
    if current_pair:
        user_bot_pairs.append(current_pair)

    # Apply sliding window: keep last MAX_CONVERSATION_HISTORY pairs
    if len(user_bot_pairs) > MAX_CONVERSATION_HISTORY:
        user_bot_pairs = user_bot_pairs[-MAX_CONVERSATION_HISTORY:]
        log.debug(f"Applied sliding window: kept last {MAX_CONVERSATION_HISTORY} user+bot pairs from {len(conversations)} total conversations")
    else:
        log.debug(f"Using all {len(user_bot_pairs)} user+bot pairs (within limit of {MAX_CONVERSATION_HISTORY})")

    # Convert pairs to messages
    for pair in user_bot_pairs:
        for conv in pair:
            role = conv.get("role", "")
            content = conv.get("content", "")

            if role == "user_query":
                messages.append(HumanMessage(content=content))
            elif role == "bot_response":
                messages.append(AIMessage(content=content))

    # ALWAYS add ALL reference data (from entire history, not just window)
    if all_reference_data:
        ref_data_text = _format_reference_data(all_reference_data, log)
        # Append reference data to the last AI message if exists, otherwise create a new message
        if messages and isinstance(messages[-1], AIMessage):
            # Append to existing AI message
            messages[-1].content = messages[-1].content + "\n\n" + ref_data_text
        else:
            # Create a new message with reference data (though this shouldn't happen)
            messages.append(AIMessage(content=ref_data_text))
        log.debug(f"📎 Included {len(all_reference_data)} reference items from entire conversation history")

    return messages


def _format_reference_data(all_reference_data: list[dict], log: logging.Logger) -> str:
    """
    Format reference data for inclusion in planner messages.

    Surfaces IDs, keys and timestamps that the planner should use directly
    instead of fetching them again.  Every supported type is shown so the
    planner has full context for tool argument construction.
    """
    if not all_reference_data:
        return ""

    # Group by type
    spaces       = [d for d in all_reference_data if d.get("type") == "confluence_space"]
    pages        = [d for d in all_reference_data if d.get("type") == "confluence_page"]
    projects     = [d for d in all_reference_data if d.get("type") == "jira_project"]
    issues       = [d for d in all_reference_data if d.get("type") == "jira_issue"]
    channels     = [d for d in all_reference_data if d.get("type") == "slack_channel"]
    msg_timestamps = [d for d in all_reference_data if d.get("type") == "slack_message_ts"]

    max_items = 10
    lines = ["## Reference Data (use these IDs/keys directly — do NOT fetch them again):"]

    if spaces:
        # Show both numeric id (for space_id) and key (accepted by get_pages_in_space)
        parts = []
        for item in spaces[:max_items]:
            space_id  = item.get("id", "")
            space_key = item.get("key", "")
            name      = item.get("name", "?")
            # Build a compact representation with all available identifiers
            id_str    = f"id={space_id}" if space_id else ""
            key_str   = f"key={space_key}" if space_key else ""
            identifiers = ", ".join(filter(None, [id_str, key_str]))
            parts.append(f"{name} ({identifiers})")
        lines.append(f"**Confluence Spaces** (use numeric `id` for space_id, or `key` for get_pages_in_space): {', '.join(parts)}")

    if pages:
        parts = [
            f"{item.get('name', '?')} (id={item.get('id', '?')}, space_key={item.get('key', '?')})"
            for item in pages[:max_items]
        ]
        lines.append(f"**Confluence Pages** (use `id` for page_id): {', '.join(parts)}")

    if projects:
        parts = [
            f"{item.get('name', '?')} (key={item.get('key', '?')})"
            for item in projects[:max_items]
        ]
        lines.append(f"**Jira Projects** (use `key`): {', '.join(parts)}")

    if issues:
        parts = [item.get("key", "?") for item in issues[:max_items]]
        lines.append(f"**Jira Issues** (use `key`): {', '.join(parts)}")

    if channels:
        parts = [
            f"{item.get('name', item.get('id', '?'))} (id={item.get('id', '?')})"
            for item in channels[:max_items]
        ]
        lines.append(f"**Slack Channels** (use `id` for channel parameter): {', '.join(parts)}")

    if msg_timestamps:
        parts = [
            f"{item.get('name', 'ts')}={item.get('id', '?')}"
            for item in msg_timestamps[:max_items]
        ]
        lines.append(f"**Slack Message Timestamps** (use as `thread_ts` for reply_to_message): {', '.join(parts)}")

    log.debug(
        f"📎 Reference data: {len(spaces)} spaces, {len(pages)} pages, "
        f"{len(projects)} jira projects, {len(issues)} jira issues, "
        f"{len(channels)} slack channels, {len(msg_timestamps)} slack ts"
    )

    return "\n".join(lines)


def _build_planner_messages(state: ChatState, query: str, log: logging.Logger) -> list[HumanMessage | AIMessage | SystemMessage]:
    """Build LangChain messages for planner with conversation context - using message format for better context awareness

    Returns:
        List of messages: [SystemMessage (optional), ...conversation messages..., HumanMessage (current query + context)]
    """
    previous_conversations = state.get("previous_conversations", [])
    messages = []

    # Convert conversation history to LangChain messages (with sliding window)
    if previous_conversations:
        conversation_messages = _build_conversation_messages(previous_conversations, log)
        messages.extend(conversation_messages)
        log.debug(f"Using {len(conversation_messages)} messages from {len(previous_conversations)} conversations (sliding window applied)")

    # Build current query message
    user_context = _format_user_context(state)
    query_content = f"{query}\n\n{user_context}" if user_context else query

    # Add current query as HumanMessage
    messages.append(HumanMessage(content=query_content))

    return messages


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


def _extract_missing_params_from_error(error_msg: str) -> list[str]:
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


def _extract_invalid_params_from_args(args: dict, error_msg: str) -> list[str]:
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
    """
    Build the context injected into the planner prompt when re-planning after a
    partial iteration (continue_with_more_tools).

    Design principles:
    - Generic: works for any tool combination, not Jira/email specific.
    - No truncation: every tool result is emitted in full so the planner has
      complete information to chain calls and generate write content.
    - Retrieval knowledge is surfaced from state["final_results"] (the
      deduplicated merged blocks) AND from the raw tool result string so
      nothing is lost.
    - Completed write/action tools are flagged to prevent accidental repeats.
    """
    tool_results = state.get("all_tool_results", [])
    if not tool_results:
        return ""

    # ── Classify results: retrieval vs everything else ────────────────────────
    def _is_retrieval(tool_name: str) -> bool:
        name = tool_name.lower()
        return "retrieval" in name or "search_internal_knowledge" in name

    retrieval_results = [r for r in tool_results if _is_retrieval(r.get("tool_name", ""))]
    api_results       = [r for r in tool_results if not _is_retrieval(r.get("tool_name", ""))]

    parts = []

    # ══════════════════════════════════════════════════════════════════════════
    # Section 1 — Retrieved knowledge
    # Prefer state["final_results"] (merged/deduplicated blocks) but also
    # include the raw tool result text so nothing is omitted.
    # ══════════════════════════════════════════════════════════════════════════
    final_results = state.get("final_results", []) or []

    if retrieval_results or final_results:
        parts.append("## 📚 RETRIEVED KNOWLEDGE")
        parts.append(
            "Use this as the authoritative source when generating content for "
            "any write action (create, update, send, post, comment, etc.). "
            "Write the full content inline — do NOT summarise or reduce to bullet points."
        )
        parts.append("")

        knowledge_written = False

        # 1a. Emit every block from final_results (no limit, no truncation)
        if final_results:
            knowledge_lines = []
            for i, block in enumerate(final_results):
                text = ""
                if isinstance(block, dict):
                    text = (
                        block.get("text", "")
                        or block.get("content", "")
                        or block.get("chunk", "")
                        or ""
                    )
                    if not text and "blocks" in block:
                        # Nested block list (e.g. Confluence page structure)
                        text = "\n".join(
                            b.get("text", "") for b in block["blocks"] if isinstance(b, dict)
                        )
                text = str(text).strip()
                if text:
                    knowledge_lines.append(f"[KB-{i+1}]\n{text}")
            if knowledge_lines:
                parts.append("\n\n".join(knowledge_lines))
                knowledge_written = True

        # 1b. Always also emit the full raw retrieval result strings so
        #     nothing is lost if final_results was populated differently.
        for r in retrieval_results:
            if r.get("status") == "success":
                raw = str(r.get("result", "")).strip()
                if raw:
                    parts.append(f"\n[Raw retrieval output from {r.get('tool_name', 'retrieval')}]\n{raw}")
                    knowledge_written = True

        if not knowledge_written:
            parts.append("(No knowledge content retrieved yet.)")

        parts.append("")

    # ══════════════════════════════════════════════════════════════════════════
    # Section 2 — All other tool results (full, untruncated)
    # ══════════════════════════════════════════════════════════════════════════
    if api_results:
        parts.append("## 🔧 TOOL RESULTS")
        parts.append(
            "Extract any IDs, keys, references, or values you need for the next steps "
            "directly from the results below."
        )
        parts.append("")
        for result in api_results:
            tool_name   = result.get("tool_name", "unknown")
            status      = result.get("status", "unknown")
            result_data = result.get("result", "")

            # Emit in full — no character cap
            if isinstance(result_data, dict):
                result_str = json.dumps(result_data, default=str, indent=2)
            else:
                result_str = str(result_data)

            parts.append(f"### {tool_name} ({status})\n{result_str}")
        parts.append("")

    # ══════════════════════════════════════════════════════════════════════════
    # Section 3 — Duplicate-prevention guard for already-completed tools
    # ══════════════════════════════════════════════════════════════════════════
    completed_tools = [
        r.get("tool_name", "unknown")
        for r in tool_results
        if r.get("status") == "success"
    ]
    if completed_tools:
        parts.append(
            "⚠️ ALREADY COMPLETED — DO NOT REPEAT: The following tools already "
            "succeeded. Planning them again will create duplicates:\n" +
            "\n".join(f"  ✅ {t}" for t in completed_tools) +
            "\nOnly plan the remaining incomplete steps."
        )
        parts.append("")

    # ══════════════════════════════════════════════════════════════════════════
    # Section 4 — Generic planning instructions (tool-agnostic)
    # ══════════════════════════════════════════════════════════════════════════
    parts.append("## 📋 PLANNING INSTRUCTIONS FOR THIS CYCLE")
    parts.append(
        "1. Use the TOOL RESULTS above to extract any identifiers (IDs, keys, URLs, "
        "addresses, timestamps, etc.) needed for subsequent tool calls.\n"
        "2. When a write tool needs content (email body, Jira comment, Confluence page, "
        "Slack message, etc.), write the FULL content INLINE in the tool arguments. "
        "Draw from the RETRIEVED KNOWLEDGE shown above — use it verbatim or synthesize "
        "it into well-structured prose. Do NOT summarize to bullet points or leave "
        "placeholders. The retrieved text above IS the authoritative source — use it.\n"
        "3. ⚠️ CRITICAL: Do NOT hallucinate or generate content from your own training "
        "knowledge for write actions. ONLY use content from the RETRIEVED KNOWLEDGE "
        "section above. If information is not in the retrieved knowledge, say so.\n"
        "4. Use `{{tool_name.data.field[0].subfield}}` placeholder syntax ONLY for "
        "referencing identifiers/keys (IDs, issue keys, thread IDs, etc.) from previous "
        "tool results, NEVER for content fields.\n"
        "5. Do NOT re-fetch or re-retrieve data that is already present above.\n"
        "6. Do NOT repeat any tool listed in the ALREADY COMPLETED section."
    )

    return "\n".join(parts)


async def _plan_with_validation_retry(
    llm: BaseChatModel,
    system_prompt: str,
    messages: list[HumanMessage | AIMessage | SystemMessage],
    state: ChatState,
    log: logging.Logger,
    query: str,
    writer: StreamWriter | None = None,
    config: RunnableConfig | None = None
) -> dict[str, Any]:
    """
    Plan with tool validation retry loop.

    If planner suggests invalid tools, retry with error message showing available tools.

    Args:
        llm: The language model to use
        system_prompt: System prompt with tool descriptions
        messages: List of conversation messages (HumanMessage, AIMessage) - conversation history + current query
        state: Chat state
        log: Logger instance
        query: Current user query (for error messages)
    """
    validation_retry_count = state.get("tool_validation_retry_count", 0)
    max_retries = NodeConfig.MAX_VALIDATION_RETRIES

    invoke_config = {"callbacks": [_opik_tracer]} if _opik_tracer else {}

    while validation_retry_count <= max_retries:
        try:
            # Build message list: SystemMessage + conversation history + current query
            llm_messages = [SystemMessage(content=system_prompt), *messages]

            # Keepalive prevents SSE timeout during LLM planning call
            keepalive_task = asyncio.create_task(
                send_keepalive(writer, config, "Planning actions...")
            )
            try:
                # Call LLM with full conversation context as messages
                response = await asyncio.wait_for(
                    llm.ainvoke(llm_messages, config=invoke_config),
                    timeout=NodeConfig.PLANNER_TIMEOUT_SECONDS
                )
            finally:
                keepalive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await keepalive_task

            # Parse response
            plan = _parse_planner_response(
                response.content if hasattr(response, 'content') else str(response),
                log
            )

            # Validate tools
            tools = plan.get('tools', [])

            # Fix empty retrieval queries in fallback plans
            for tool in tools:
                if "retrieval" in tool.get("name", "").lower() and not tool.get("args", {}).get("query", "").strip():
                    tool["args"]["query"] = query  # Use original user query
                    log.info(f"🔧 Fixed empty retrieval query with user query: {query[:50]}")

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
                # Prepend error message to the last HumanMessage
                if messages and isinstance(messages[-1], HumanMessage):
                    messages[-1].content = error_message + "\n\n" + messages[-1].content
                else:
                    # If no HumanMessage exists, create one
                    messages.append(HumanMessage(content=error_message))

        except asyncio.TimeoutError:
            log.warning("⏱️ Planner timeout")
            fallback = _create_fallback_plan(query, state)
            fallback_tools = fallback.get('tools', [])
            is_valid, invalid_tools, _ = _validate_planned_tools(fallback_tools, state, log)
            if not is_valid:
                log.warning(f"⚠️ Fallback tools unavailable: {invalid_tools}. Switching to direct answer.")
                fallback['tools'] = [t for t in fallback_tools if isinstance(t, dict) and t.get('name', '') not in invalid_tools]
                if not fallback['tools']:
                    fallback['can_answer_directly'] = True
            return fallback
        except Exception as e:
            log.error(f"💥 Planner error: {e}")
            fallback = _create_fallback_plan(query, state)
            fallback_tools = fallback.get('tools', [])
            is_valid, invalid_tools, _ = _validate_planned_tools(fallback_tools, state, log)
            if not is_valid:
                log.warning(f"⚠️ Fallback tools unavailable: {invalid_tools}. Switching to direct answer.")
                fallback['tools'] = [t for t in fallback_tools if isinstance(t, dict) and t.get('name', '') not in invalid_tools]
                if not fallback['tools']:
                    fallback['can_answer_directly'] = True
            return fallback

    # Should never reach here
    fallback = _create_fallback_plan(query, state)
    fallback_tools = fallback.get('tools', [])
    is_valid, invalid_tools, _ = _validate_planned_tools(fallback_tools, state, log)
    if not is_valid:
        fallback['tools'] = [t for t in fallback_tools if isinstance(t, dict) and t.get('name', '') not in invalid_tools]
        if not fallback['tools']:
            fallback['can_answer_directly'] = True
    return fallback


def _parse_planner_response(content: str, log: logging.Logger) -> dict[str, Any]:
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

    # Handle multiple JSON objects - extract the first complete one
    # Sometimes LLM outputs multiple JSON objects concatenated (e.g., {"a":1}\n{"b":2})
    if content.count('{') > 1:
        # Try to find the first complete JSON object
        brace_count = 0
        start_idx = -1
        found_valid = False
        for i, char in enumerate(content):
            if char == '{':
                if brace_count == 0:
                    start_idx = i
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0 and start_idx >= 0:
                    # Found a complete JSON object
                    try:
                        json_str = content[start_idx:i+1]
                        test_plan = json.loads(json_str)
                        if isinstance(test_plan, dict) and (
                            # Prefer objects with "tools" field, but accept any valid dict
                            "tools" in test_plan or not found_valid
                        ):
                            content = json_str  # Use this JSON object
                            found_valid = True
                            log.debug(f"Extracted first complete JSON object from multiple JSON responses (length: {len(json_str)})")
                            if "tools" in test_plan:
                                break  # Found one with tools, use it
                    except json.JSONDecodeError:
                        continue
        # If we found a valid one, content is already updated
        if not found_valid:
            log.warning("Multiple JSON objects detected but none were valid, trying original content")

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
            normalized_tools = [
                {"name": tool["name"], "args": tool.get("args", {})}
                for tool in plan.get("tools", [])
                if isinstance(tool, dict) and "name" in tool
            ]

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


def _create_fallback_plan(query: str, state: "ChatState | None" = None) -> dict[str, Any]:
    """Create a context-aware fallback plan when the planner times out or fails.

    Decision tree:
    1. If retrieval was already executed this turn AND action tools are available
       → plan those action tools (don't repeat retrieval endlessly).
    2. If retrieval was already executed but no action tools match the query intent
       → can_answer_directly so the LLM at least responds with retrieved knowledge.
    3. If retrieval has NOT been executed yet and knowledge is configured
       → default to retrieval (original behaviour).
    4. No knowledge, no state → can_answer_directly.
    """
    # ── 1. Identify what was already executed this turn ──────────────────────
    all_tool_results = []
    has_knowledge = False
    if state:
        all_tool_results = state.get("all_tool_results", []) or []
        has_knowledge = state.get("has_knowledge", False)

    executed_names = {
        r.get("tool_name", "") for r in all_tool_results if isinstance(r, dict)
    }
    retrieval_done = any(
        "retrieval" in name or "search_internal_knowledge" in name
        for name in executed_names
    )

    # ── 2. After retrieval: try to plan action tools ──────────────────────────
    if retrieval_done and state:
        try:
            from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas
            available = {getattr(t, "name", "") for t in get_agent_tools_with_schemas(state)}
        except Exception:
            available = set()

        query_lower = (query or "").lower()
        fallback_tools = []

        # Email intent
        wants_email = any(w in query_lower for w in ["email", "mail", "send", "reply"])
        if wants_email:
            for candidate in ["gmail.reply", "gmail.send_email", "gmail.draft_email"]:
                if candidate in available:
                    fallback_tools.append({
                        "name": candidate,
                        "args": {
                            "to": "{{previous_email_sender}}",
                            "subject": "Re: {{previous_email_subject}}",
                            "body": "{{detailed_content_from_knowledge}}"
                        }
                    })
                    break

        # Ticket/Jira intent
        wants_ticket = any(w in query_lower for w in ["ticket", "jira", "issue", "update", "comment"])
        if wants_ticket:
            for candidate in ["jira.update_issue", "jira.add_comment"]:
                if candidate in available:
                    fallback_tools.append({
                        "name": candidate,
                        "args": {
                            "issue_key": "{{jira_issue_key_from_context}}",
                            "description": "{{detailed_content_from_knowledge}}"
                        }
                    })
                    break

        if fallback_tools:
            return {
                "intent": "Fallback: Execute action after retrieval",
                "reasoning": "Retrieval complete; timeout fallback proceeding with write actions",
                "can_answer_directly": False,
                "needs_clarification": False,
                "clarifying_question": "",
                "tools": fallback_tools,
            }

        # Retrieval done but no matching action tools found → respond with knowledge
        return {
            "intent": "Fallback: Respond with retrieved knowledge",
            "reasoning": "Retrieval complete; planner timeout — responding directly",
            "can_answer_directly": True,
            "needs_clarification": False,
            "clarifying_question": "",
            "tools": [],
        }

    # ── 3. Retrieval not yet done — use it if knowledge is configured ─────────
    if has_knowledge:
        return {
            "intent": "Fallback: Search internal knowledge",
            "reasoning": "Planner failed; searching knowledge base",
            "can_answer_directly": False,
            "needs_clarification": False,
            "clarifying_question": "",
            "tools": [{"name": "retrieval.search_internal_knowledge", "args": {"query": query}}],
        }

    # ── 4. No knowledge, no context — answer directly ────────────────────────
    return {
        "intent": "Fallback: Direct answer",
        "reasoning": "Planner failed; no knowledge configured",
        "can_answer_directly": True,
        "needs_clarification": False,
        "clarifying_question": "",
        "tools": [],
    }


def _validate_planned_tools(
    planned_tools: list[dict[str, Any]],
    state: ChatState,
    log: logging.Logger
) -> tuple[bool, list[str], list[str]]:
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

        # Get available tool names (both sanitized and original, like tools_by_name in execution)
        available_tool_names = set()
        for tool in tools:
            sanitized_name = getattr(tool, 'name', str(tool))
            available_tool_names.add(sanitized_name)
            # Also add original name if different (like tools_by_name does)
            original_name = getattr(tool, '_original_name', sanitized_name)
            if original_name != sanitized_name:
                available_tool_names.add(original_name)

        # Check for invalid tools — same 2-step resolution as execution
        invalid_tools = []
        for tool_call in planned_tools:
            if isinstance(tool_call, dict):
                tool_name = tool_call.get('name', '')
                found = (
                    tool_name in available_tool_names
                    or (_sanitize_tool_name_if_needed(tool_name, llm, state) if llm else tool_name)
                    in available_tool_names
                )
                if not found:
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


def _has_slack_tools(state: ChatState) -> bool:
    """Check if Slack tools available"""
    agent_toolsets = state.get("agent_toolsets", [])
    return any(isinstance(ts, dict) and "slack" in ts.get("name", "").lower() for ts in agent_toolsets)


def _has_onedrive_tools(state: ChatState) -> bool:
    """Check if OneDrive tools available"""
    agent_toolsets = state.get("agent_toolsets", [])
    return any(isinstance(ts, dict) and "onedrive" in ts.get("name", "").lower() for ts in agent_toolsets)


def _has_outlook_tools(state: ChatState) -> bool:
    """Check if Outlook tools available"""
    agent_toolsets = state.get("agent_toolsets", [])
    return any(isinstance(ts, dict) and "outlook" in ts.get("name", "").lower() for ts in agent_toolsets)

def _has_teams_tools(state: ChatState) -> bool:
    """Check if Microsoft Teams tools available"""
    agent_toolsets = state.get("agent_toolsets", [])
    return any(isinstance(ts, dict) and "teams" in ts.get("name", "").lower() for ts in agent_toolsets)

def _has_github_tools(state: ChatState) -> bool:
    """Check if GitHub tools available"""
    agent_toolsets = state.get("agent_toolsets", [])
    return any(isinstance(ts, dict) and "github" in ts.get("name", "").lower() for ts in agent_toolsets)

def _has_mariadb_tools(state: ChatState) -> bool:
    """Check if MariaDB tools available"""
    agent_toolsets = state.get("agent_toolsets", [])
    return any(isinstance(ts, dict) and "mariadb" in ts.get("name", "").lower() for ts in agent_toolsets)


def _has_clickup_tools(state: ChatState) -> bool:
    """Check if ClickUp tools available"""
    agent_toolsets = state.get("agent_toolsets", [])
    return any(isinstance(ts, dict) and "clickup" in ts.get("name", "").lower() for ts in agent_toolsets)
def _has_redshift_tools(state: ChatState) -> bool:
    """Check if Redshift tools available"""
    agent_toolsets = state.get("agent_toolsets", [])
    return any(isinstance(ts, dict) and "redshift" in ts.get("name", "").lower() for ts in agent_toolsets)


def _build_knowledge_context(state: ChatState, log: logging.Logger) -> str:
    """
    Build knowledge context for the planner prompt.

    Fully data-driven — no hardcoded per-app rules.
    Derives guidance from what is actually configured:
      - agent_knowledge  → what is indexed (retrieval sources)
      - agent_toolsets   → what live API tools exist
    """
    agent_knowledge: list = state.get("agent_knowledge", []) or []
    agent_toolsets: list  = state.get("agent_toolsets", []) or []

    if not agent_knowledge:
        return ""

    # ── 1. Classify knowledge sources ────────────────────────────────────────
    # KB = document stores; everything else = app connector snapshot
    kb_sources: list[str] = []
    indexed_apps: list[dict] = []   # {"label": str, "type_key": str, "connector_id": str}

    for k in agent_knowledge:
        if not isinstance(k, dict):
            continue
        name     = k.get("displayName") or k.get("name") or ""
        ktype    = (k.get("type") or "").strip()
        ktype_up = ktype.upper()
        connector_id = (k.get("connectorId") or "").strip()

        if ktype_up == "KB":
            kb_sources.append(name or "Knowledge Base (Collection)")
        else:
            # Normalise to lowercase single-word key (e.g. "DRIVE WORKSPACE" → "drive")
            type_key = ktype.lower().split()[0] if ktype else ""
            label    = name or type_key.capitalize() or "App Connector"
            indexed_apps.append({"label": label, "type_key": type_key, "connector_id": connector_id})

    indexed_type_keys = {a["type_key"] for a in indexed_apps if a["type_key"]}

    # ── 2. Classify live API toolsets ────────────────────────────────────────
    # Build: type_key → list of tool names available for that app
    api_tools_by_type: dict[str, list[str]] = {}

    for ts in agent_toolsets:
        if not isinstance(ts, dict):
            continue
        ts_name = (ts.get("name") or "").strip().lower()
        if not ts_name or ts_name in ("retrieval", "calculator"):
            continue

        # Normalise toolset name to type_key (same logic as knowledge)
        ts_key   = ts_name.split()[0]
        ts_tools = ts.get("tools", [])
        tool_names = [
            t.get("fullName") or f"{ts_key}.{t.get('toolName') or t.get('name', '')}"
            for t in ts_tools
            if isinstance(t, dict)
        ]
        if not tool_names:
            tool_names = [f"{ts_key}.*"]

        api_tools_by_type.setdefault(ts_key, []).extend(tool_names)

    overlapping_keys = indexed_type_keys & set(api_tools_by_type.keys())

    # ── 3. Build context block ────────────────────────────────────────────────
    lines: list[str] = [
        "",
        "## 🧠 KNOWLEDGE & DATA SOURCES",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    # --- Indexed knowledge (retrieval) ---
    if kb_sources or indexed_apps:
        lines.append(
            "\n### 📚 INDEXED KNOWLEDGE → `retrieval.search_internal_knowledge`"
        )
        lines.append(
            "Retrieval performs **semantic search** across ALL indexed sources at once.\n"
            "Use it when the query asks *what is / find / search by topic or keyword*.\n"
            "⚠️  Retrieval returns a **snapshot** — it may lag behind the live system."
        )

        if kb_sources:
            lines.append("\n**Knowledge Bases (always searched, no filter needed):**")
            lines.extend(f"  - 📄 {kb}" for kb in kb_sources)

        if indexed_apps:
            lines.append(
                "\n**Indexed App Connectors** (text is searchable via retrieval):\n"
                "  Use the type_key or connector_id in `filters.apps` for retrieval:"
            )
            lines.extend(
                f"  - 🔗 `{app['type_key']}` ({app['label']}) — connector_id: `{app['connector_id']}`"
                for app in indexed_apps
            )
        else:
            lines.append(
                "\n⚠️  **NO app connectors are indexed** — only Knowledge Bases above are available.\n"
                "  → When calling `retrieval.search_internal_knowledge`, do NOT set `filters.apps`.\n"
                "  → Use `filters: {{}}` or omit filters entirely so the KB is searched."
            )

    # --- Live API toolsets ---
    if api_tools_by_type:
        lines.append(
            "\n### ⚡ LIVE API TOOLS → service-specific tool calls"
        )
        lines.append(
            "Use live API tools when the query needs:\n"
            "  • **Current state** — data that must be up-to-date right now\n"
            "  • **Exact lookup by ID / key** — e.g. get issue PA-123\n"
            "  • **Filtered lists** — my open tickets, this sprint, unread emails\n"
            "  • **Write actions** — create, update, delete, comment, send, assign"
        )
        for ts_key, tool_names in api_tools_by_type.items():
            # Show up to 5 representative tool names
            MAX_TOOLS = 5
            sample = ", ".join(tool_names[:5])
            more   = f" … (+{len(tool_names)-5} more)" if len(tool_names) > MAX_TOOLS else ""
            lines.append(f"  - 🛠️ **{ts_key.capitalize()}**: {sample}{more}")

    # --- Overlap guidance (apps with BOTH indexed AND live API) ---
    if overlapping_keys:
        lines.append(
            "\n### 🔀 DUAL-SOURCE APPS — Use the right source(s) for the intent"
        )
        lines.append(
            "These apps have **BOTH** indexed content (searchable via retrieval) **AND** live API tools.\n"
            "Choose based on what the user actually wants:\n"
            "\n"
            "| User intent | What to use |\n"
            "|---|---|\n"
            "| **SERVICE NOUN** without explicit search verb — '[topic] tickets', '[topic] issues', '[topic] pages' | BOTH retrieval + live API search (parallel) |\n"
            "| **FIND / SEARCH** content by topic or keyword — 'find pages about X', 'search for issues about Y' | BOTH retrieval + live API search (parallel) |\n"
            "| **LIVE / CURRENT** data — 'list my open tickets', 'show recent changes', 'assigned to me' | live API only |\n"
            "| **LOOKUP** by exact ID or key — PA-123, page id 12345 | live API only |\n"
            "| **WRITE ACTION** — create, update, delete, comment, send, assign | live API write tool only |\n"
            "| **INFORMATION** — 'what is X', 'tell me about Y', 'explain Z' (no service resource noun) | retrieval only |\n"
        )
        for key in sorted(overlapping_keys):
            label = next(
                (a["label"] for a in indexed_apps if a["type_key"] == key),
                key.capitalize()
            )
            tool_sample = api_tools_by_type.get(key, [])[:4]
            lines.append(
                f"  **{label}**: retrieval → topic/historical search; "
                f"live API ({', '.join(tool_sample)}) → current data, exact IDs, write actions; "
                f"BOTH → when user uses a service resource noun ('[topic] tickets', '[topic] issues', '[topic] pages') OR explicitly asks to find/search content by topic"
            )

    # --- Hybrid search guidance: when to combine retrieval + live search APIs ---
    # Only relevant when user explicitly wants to find/search/discover content.
    has_retrieval = bool(kb_sources or indexed_apps)
    # Collect search-capable tools from non-overlapping toolsets
    non_overlap_search_tools: dict[str, list[str]] = {}
    for ts_key, tool_names in api_tools_by_type.items():
        search_tools = [
            t for t in tool_names
            if "search" in t.split(".")[-1].lower()
        ]
        if search_tools:
            non_overlap_search_tools[ts_key] = search_tools

    if has_retrieval and non_overlap_search_tools:
        lines.append(
            "\n### 🔍 HYBRID SEARCH — when to combine retrieval + live search APIs"
        )
        lines.append(
            "Use **BOTH** `retrieval.search_internal_knowledge` AND a live search API **in parallel** when:\n"
            "  • User uses a **service resource noun** — 'tickets', 'issues', 'bugs', 'epics', 'pages', 'spaces' — even without an explicit verb\n"
            "  • Example: '[topic] tickets', '[topic] issues', '[topic] pages' → use BOTH retrieval + the matching service search API\n"
            "  • User explicitly asks to **FIND or SEARCH** content in a specific service\n"
            "  • User asks 'find pages/tickets/docs about [topic]'\n"
            "  • User asks 'search [app] for [X]' or 'is there anything about [topic] in [app]'\n"
            "\n"
            "**Do NOT use live search APIs for:**\n"
            "  • General information queries ('what is X', 'tell me about Y') with NO service resource noun — retrieval is sufficient\n"
            "  • Queries with no service reference and no service noun — use retrieval only\n"
            "\n"
            "**Available live search APIs:**"
        )
        for ts_key, search_tools in sorted(non_overlap_search_tools.items()):
            tool_list = ", ".join(f"`{t}`" for t in search_tools[:4])
            lines.append(f"  - 🔍 **{ts_key.capitalize()}**: {tool_list}")

        lines.append(
            "\n**EXAMPLE** — 'find pages about OneDrive configuration' (explicit search in service):\n"
            "```json\n"
            "[\n"
            "  {{\"name\": \"retrieval.search_internal_knowledge\", \"args\": {{\"query\": \"OneDrive configuration\", \"filters\": {{}}}}}},\n"
            "  {{\"name\": \"confluence.search_content\", \"args\": {{\"query\": \"OneDrive configuration\"}}}}\n"
            "]\n"
            "```\n"
            "**EXAMPLE** — 'what is our OneDrive configuration?' (information query, NOT explicit search):\n"
            "```json\n"
            "[{{\"name\": \"retrieval.search_internal_knowledge\", \"args\": {{\"query\": \"OneDrive configuration\"}}}}]\n"
            "```"
        )

    # --- Universal decision rule (always shown) ---
    lines.append(
        "\n### 🎯 TOOL SELECTION SUMMARY\n"
        "```\n"
        "Greeting / thanks / meta-question about conversation                           →  can_answer_directly: true\n"
        "Write action (create/update/delete/send/assign)                                →  live API write tool\n"
        "Live/current data (list mine, open, this sprint, recent)                       →  live API read tool\n"
        "Lookup by exact ID or key (e.g. PA-123)                                        →  live API read tool\n"
        "[topic] tickets / [topic] issues / [topic] pages (service noun, dual-source)   →  BOTH retrieval + live search API (parallel)\n"
        "FIND/SEARCH [service] content by topic or keyword                              →  BOTH retrieval + live search API (parallel)\n"
        "General information query — 'what is X', 'tell me about Y' (no service noun)   →  retrieval (DEFAULT)\n"
        "Ambiguous / unclear intent                                                     →  retrieval (DEFAULT)\n"
        "```\n"
        "⚠️ **RETRIEVAL FILTER RULE**:\n"
        "   • `filters.apps` should ONLY contain app names listed in 'Indexed App Connectors' above.\n"
        "   • If no app connectors are indexed (only KB), use `\"filters\": {{}}` (empty).\n"
        "   • NEVER set `filters.apps` to a live-API-only service.\n"
        "\n"
        "⚠️ **EFFICIENCY**: If a previous tool already returned IDs/keys, use them\n"
        "   directly in the next write tool. Do NOT re-fetch items you already have."
    )

    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)

# Tool description caching
_tool_description_cache: dict[str, str] = {}


def _get_cached_tool_descriptions(state: ChatState, log: logging.Logger) -> str:
    """Get tool descriptions with caching"""
    org_id = state.get("org_id", "default")
    agent_toolsets = state.get("agent_toolsets", [])
    llm = state.get("llm")

    has_knowledge = state.get("has_knowledge", False)

    from app.modules.agents.qna.tool_system import (
        _requires_sanitized_tool_names,
        get_agent_tools_with_schemas,
    )

    llm_type = "anthropic" if llm and _requires_sanitized_tool_names(llm) else "other"
    toolset_names = sorted([ts.get("name", "") for ts in agent_toolsets if isinstance(ts, dict)])
    # Include has_knowledge in cache key — a change in knowledge config must bust the cache
    cache_key = f"{org_id}_{hash(tuple(toolset_names))}_{llm_type}_{has_knowledge}"

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


def _get_field_type_name(field_info: object) -> str:
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
            return type_str.replace('<class ', '').replace('>', '').replace("'", "")
    except Exception:
        return "any"


def _get_field_type_name_v1(field_info: object) -> str:
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


def _extract_parameters_from_schema(schema: dict[str, Any] | type, log: logging.Logger) -> dict[str, dict[str, Any]]:
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


def _format_tool_descriptions(tools: list, log: logging.Logger) -> str:
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
            desc_text = description
            lines.append(f"  {desc_text}")

        # Extract parameter schema
        try:
            schema = getattr(tool, 'args_schema', None)
            if schema:
                params_info = _extract_parameters_from_schema(schema, log)
                if params_info:
                    # Add blank line between description and parameters
                    lines.append("")
                    lines.append("  **Parameters:**")
                    for param_name, param_info in params_info.items():
                        required_marker = "**required**" if param_info.get("required") else "optional"
                        param_type = param_info.get("type", "any").upper()
                        param_desc = param_info.get("description", "")

                        # Format: - param_name (required): description [TYPE]
                        if param_desc:
                            lines.append(f"  - `{param_name}` ({required_marker}): {param_desc} [{param_type}]")
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

    # Build informative execution status
    tool_names = [tool.get("name", "") for tool in planned_tools]
    if len(tool_names) == 1:
        tool_name = tool_names[0]
        if "retrieval" in tool_name.lower():
            status_msg = "Searching knowledge base for relevant information..."
        elif "confluence" in tool_name.lower():
            if "create" in tool_name.lower():
                status_msg = "Creating Confluence page..."
            elif "update" in tool_name.lower():
                status_msg = "Updating Confluence page..."
            else:
                status_msg = "Accessing Confluence content..."
        elif "jira" in tool_name.lower():
            if "create" in tool_name.lower():
                status_msg = "Creating Jira issue..."
            elif "update" in tool_name.lower():
                status_msg = "Updating Jira issue..."
            else:
                status_msg = "Accessing Jira..."
        else:
            status_msg = f"Executing {tool_name.replace('_', ' ').title()}..."
    else:
        # Multiple tools - describe what we're doing
        has_retrieval = any("retrieval" in t.lower() for t in tool_names)
        has_confluence = any("confluence" in t.lower() for t in tool_names)
        has_jira = any("jira" in t.lower() for t in tool_names)

        actions = []
        if has_retrieval:
            actions.append("searching knowledge base")
        if has_confluence:
            actions.append("working with Confluence")
        if has_jira:
            actions.append("working with Jira")

        if actions:
            status_msg = f"Executing {len(planned_tools)} operations: {', '.join(actions)}..."
        else:
            status_msg = f"Executing {len(planned_tools)} operations in parallel..."

    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "executing", "message": status_msg}
    }, config)

    # Get available tools
    try:
        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

        tools = get_agent_tools_with_schemas(state)
        llm = state.get("llm")

        # Build tool mapping: both sanitized (underscore) and original (dot) names.
        # _underscore_to_dotted is NOT applied here because it only replaces the
        # first underscore, which is wrong for multi-word app names like
        # knowledge_hub (knowledge_hub_list_files → knowledge.hub_list_files ✗).
        # The two-entry map (sanitized + original) is sufficient: the LLM outputs
        # either the sanitized or the original name and step-1 lookup always hits.
        tools_by_name = {}
        for t in tools:
            sanitized_name = getattr(t, 'name', str(t))
            original_name = getattr(t, '_original_name', sanitized_name)
            tools_by_name[sanitized_name] = t
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
    # IMPORTANT: accumulate across iterations so that retrieval results from
    # iteration 1 remain visible to the planner in iterations 2, 3, ...
    state["tool_results"] = tool_results
    existing_results = state.get("all_tool_results", []) or []
    state["all_tool_results"] = existing_results + tool_results

    # Track executed tool names for continue mode status messages
    executed_tool_names = [r.get("tool_name", "") for r in tool_results if r.get("tool_name")]
    if "executed_tool_names" in state:
        state["executed_tool_names"].extend(executed_tool_names)
    else:
        state["executed_tool_names"] = executed_tool_names

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
    cascade_errors = [r for r in tool_results if r.get("status") == "cascade_error"]

    log.info(f"📊 Tool results: {len(successful)} ✓, {len(failed)} ✗, {len(cascade_errors)} cascade")

    # Log details for debugging
    for r in successful:
        log.info(f"  ✅ {r.get('tool_name')}")
    for r in failed:
        log.info(f"  ❌ {r.get('tool_name')}: {str(r.get('result', ''))[:300]}")
    for r in cascade_errors:
        log.info(f"  🔗❌ {r.get('tool_name')}: cascade broken")

    # ========================================================================
    # PRE-CHECK: Orchestration failures override all other decisions
    # ========================================================================

    cascade_broken = [r for r in tool_results
                      if r.get("orchestration_status") == ORCHESTRATION_STATUS_CASCADE_BROKEN]
    empty_cascade_sources = [r for r in tool_results
                             if r.get("orchestration_status") == "empty_cascade_source"]

    if cascade_errors or cascade_broken:
        log.info(f"🔗 ORCHESTRATION FAILURE: {len(cascade_errors)} cascade errors detected")
        state["reflection_decision"] = "respond_error"
        state["reflection"] = {
            "decision": "respond_error",
            "reasoning": (
                f"Cascading tool chain broke: "
                f"{[r.get('tool_name') for r in (cascade_errors or cascade_broken)]}. "
                f"A multi-step operation failed because intermediate results were unavailable."
            ),
            "error_context": "cascade_broken",
            "task_complete": False,
        }
        duration_ms = (time.perf_counter() - start_time) * 1000
        log.info(f"⚡ Reflect: respond_error (cascade) - {duration_ms:.0f}ms")
        return state

    if empty_cascade_sources:
        log.info(f"🔗 EMPTY CASCADE SOURCE: {[r.get('tool_name') for r in empty_cascade_sources]}")
        # Don't hard-fail — mark state so downstream reflection knows
        state["_cascade_source_empty"] = True

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
        needs_continue = _check_if_task_needs_continue(query, executed_tools, tool_results, log, state)

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
        # GUARD: If this is a cascading chain, primary tool success alone
        # does NOT mean the task is complete.  Check the LAST tool instead.
        has_cascading = PlaceholderResolver.has_placeholders({"tools": planned_tools})

        if has_cascading:
            # For cascading chains, success = last tool succeeded with meaningful data
            last_result = tool_results[-1] if tool_results else None
            if (last_result
                    and last_result.get("status") == "success"
                    and not _is_semantically_empty(last_result.get("result"))):
                log.info("✅ Cascading chain completed: last tool has data")
                state["reflection_decision"] = "respond_success"
                state["reflection"] = {
                    "decision": "respond_success",
                    "reasoning": "Cascading chain completed — last tool returned meaningful data",
                    "task_complete": True
                }
                duration_ms = (time.perf_counter() - start_time) * 1000
                log.info(f"⚡ Reflect: respond_success (cascade complete) - {duration_ms:.0f}ms")
                return state
            else:
                log.info("🔗 Cascading chain: last tool empty/failed — skipping primary-success shortcut")
                # Fall through to error handling / LLM reflection
        else:
            # Non-cascading: original primary tool check
            primary_tool_name = planned_tools[0].get("name", "").lower()

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

        keepalive_task = asyncio.create_task(
            send_keepalive(writer, config, "Analyzing results...")
        )
        try:
            response = await asyncio.wait_for(
                llm.ainvoke([
                    SystemMessage(content=prompt),
                    HumanMessage(content="Analyze and decide.")
                ]),
                timeout=NodeConfig.REFLECTION_TIMEOUT_SECONDS
            )
        finally:
            keepalive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keepalive_task

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


def _parse_reflection_response(content: str, log: logging.Logger) -> dict[str, Any]:
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


def _check_primary_tool_success(query: str, successful: list[dict], log: logging.Logger) -> bool:
    """
    In a partial-success scenario (some tools succeeded, some failed), determine
    whether the *primary* / most important tool for the user's intent succeeded.

    If the primary tool succeeded we can proceed to respond even if secondary
    tools failed (e.g. an enrichment step or a non-critical side action).

    Strategy:
    1. Infer the primary intent from clear action verbs in the query.
    2. Check whether a tool whose name contains that intent verb succeeded.
    3. If we cannot match precisely, fall back to True if *any* tool succeeded
       (the respond node will surface partial results gracefully).

    Note: "make" is intentionally excluded — it is too ambiguous ("make a
    summary", "make a copy", etc.) and does not map reliably to a tool verb.
    """
    query_lower = (query or "").lower()
    successful_tools = [r.get("tool_name", "").lower() for r in successful]

    # Intent verb → tool-name segment that signals the primary action completed.
    # Order matters: more specific intents first.
    intent_to_tool_segment: list[tuple] = [
        # (query keywords that signal this intent, tool-name segment to look for)
        (["create", "new"],              "create"),
        (["update", "modify", "change", "edit"], "update"),
        (["delete", "remove"],           "delete"),
        (["add", "comment"],             "add"),
        (["send", "post", "notify"],     "send"),
        (["reply"],                      "reply"),
        (["assign"],                     "assign"),
        (["transition", "move"],         "transition"),
        (["publish"],                    "publish"),
        (["search", "find"],             "search"),
        (["get", "list", "fetch"],       "get"),
    ]

    for query_keywords, tool_segment in intent_to_tool_segment:
        if any(kw in query_lower for kw in query_keywords):
            # Check whether a tool with this segment succeeded
            for tool in successful_tools:
                # Match against the action part (after service prefix)
                action_part = tool.split(".", 1)[1] if "." in tool else tool
                if action_part.startswith(tool_segment + "_") or action_part == tool_segment:
                    log.debug(f"✅ Primary intent '{tool_segment}' matched succeeded tool: {tool}")
                    return True
            # Intent identified but no matching tool succeeded — stop here.
            # (The fallback below will still return True if anything succeeded.)
            break

    # Fallback: if any tool succeeded, treat the primary as done and let
    # the respond node explain partial results to the user.
    return len(successful) > 0


def _check_if_task_needs_continue(
    query: str,
    executed_tools: list[str],
    tool_results: list[dict[str, Any]],
    log: logging.Logger,
    state: dict[str, Any] | None = None
) -> bool:
    """
    Determine whether the agent needs another planning cycle.

    Returns True if there are planned tools that have not yet been executed.
    Returns False if all planned tools have been executed.
    """
    if not state:
        return False

    planned_tools = state.get("planned_tool_calls", [])
    if not planned_tools:
        return False

    # Normalize tool names for comparison (handle both dotted and underscored formats)
    planned_names = set()
    for tool in planned_tools:
        if isinstance(tool, dict):
            name = tool.get("name", "")
            planned_names.add(name)
            planned_names.add(name.replace(".", "_"))
            planned_names.add(name.replace("_", "."))

    executed_names = set(executed_tools)
    for tool_name in executed_tools:
        executed_names.add(tool_name.replace(".", "_"))
        executed_names.add(tool_name.replace("_", "."))

    if not planned_names.issubset(executed_names):
        missing = planned_names - executed_names
        log.debug(f"Some planned tools not yet executed: {missing}")
        return True

    return False


def _is_retrieval_tool(tool_name: str) -> bool:
    """Return True if the tool is an internal retrieval/search-knowledge tool."""
    name = (tool_name or "").lower()
    return "retrieval" in name or "search_internal_knowledge" in name


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
    errors = [
        {
            "tool_name": r.get("tool_name", "unknown"),
            "args": r.get("args", {}),
            "error": str(r.get("result", ""))[:300],
        }
        for r in tool_results
        if r.get("status") == "error"
    ]

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

    # Get context about what we're continuing with
    query = state.get("query", "")
    previous_tools = state.get("executed_tool_names", [])
    iteration_count = state.get("iteration_count", 0)
    max_iterations = state.get("max_iterations", NodeConfig.MAX_ITERATIONS)

    # Build informative message based on what was done and what's needed
    query_lower = query.lower()
    if previous_tools:
        last_tool = previous_tools[-1] if previous_tools else ""
        if "retrieval" in last_tool.lower():
            action_desc = "gathered information"
            next_action = "taking action" if any(word in query_lower for word in ["create", "update", "make", "add", "edit"]) else "completing the task"
        elif "create" in last_tool.lower():
            action_desc = "created resources"
            next_action = "completing additional steps"
        elif "update" in last_tool.lower():
            action_desc = "updated resources"
            next_action = "completing additional steps"
        elif "search" in last_tool.lower() or "get" in last_tool.lower():
            action_desc = "retrieved information"
            next_action = "taking action based on the information"
        else:
            action_desc = "completed previous steps"
            next_action = "continuing with next steps"

        message = f"Step {iteration_count}/{max_iterations}: After we {action_desc}, now {next_action}..."
    else:
        message = f"Step {iteration_count}/{max_iterations}: Planning next steps to complete your request..."

    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "continuing", "message": message}
    }, config)

    max_iterations = state.get("max_iterations", NodeConfig.MAX_ITERATIONS)
    log.info(f"➡️ Continue {state['iteration_count']}/{max_iterations}")

    return state


# ============================================================================
# MERGE AND NUMBER RETRIEVAL RESULTS (OPTION B)
# ============================================================================

def merge_and_number_retrieval_results(
    final_results: list[dict[str, Any]],
    log: logging.Logger
) -> list[dict[str, Any]]:
    """
    Merge and deduplicate retrieval results from multiple parallel calls.

    OPTION B: This function is called ONCE after all parallel retrieval
    calls are complete. It:
    1. Deduplicates blocks by (virtual_record_id, block_index)
    2. Groups blocks by document, ordering documents by their best relevance
       score (most relevant first). Within each document, blocks are ordered
       by block_index for readability.

    Sorting by relevance score (not by UUID string) ensures that R1 always
    refers to the most relevant document. This matters critically for
    fetch_full_record tool calls — when the LLM calls fetch_full_record(["R1"])
    it should retrieve the most relevant document, not an arbitrarily-ordered one.

    NOTE: Block numbering is done by get_message_content() (same as chatbot).
    This function only merges and sorts - no numbering here.

    Args:
        final_results: List of result dicts from multiple retrieval calls
        log: Logger instance

    Returns:
        Deduplicated and relevance-sorted results (block numbers assigned later
        by get_message_content)
    """
    if not final_results:
        return []

    # Step 1: Deduplicate by (virtual_record_id, block_index)
    seen_blocks: dict[tuple, Any] = {}
    # Track the best score seen for each document (used for document ordering)
    doc_best_score: dict[str, float] = {}
    # Track the earliest position each document appeared in the results list
    doc_first_position: dict[str, int] = {}

    for position, result in enumerate(final_results):
        virtual_record_id = result.get("virtual_record_id")
        if not virtual_record_id:
            virtual_record_id = result.get("metadata", {}).get("virtualRecordId")

        if not virtual_record_id:
            continue

        block_index = result.get("block_index", 0)
        block_key = (virtual_record_id, block_index)
        score = float(result.get("score", 0.0))

        # Track best score per document for ordering
        if virtual_record_id not in doc_best_score or score > doc_best_score[virtual_record_id]:
            doc_best_score[virtual_record_id] = score

        # Track earliest position per document as tiebreaker
        if virtual_record_id not in doc_first_position:
            doc_first_position[virtual_record_id] = position

        # Keep the first occurrence (or the one with highest score if duplicate)
        if block_key not in seen_blocks:
            seen_blocks[block_key] = result
        else:
            existing_score = seen_blocks[block_key].get("score", 0.0)
            if score > existing_score:
                seen_blocks[block_key] = result

    # Step 2: Sort documents by relevance (best score desc, then earliest position asc)
    # Within each document, sort blocks by block_index for natural reading order.
    deduplicated = list(seen_blocks.values())

    def sort_key(x: dict[str, Any]) -> tuple:
        vid = x.get("virtual_record_id") or x.get("metadata", {}).get("virtualRecordId", "")
        best_score = doc_best_score.get(vid, 0.0)
        first_pos = doc_first_position.get(vid, 999999)
        block_idx = x.get("block_index", 0)
        # Primary: highest score first (-best_score), Secondary: earliest position, Tertiary: block_index
        return (-best_score, first_pos, block_idx)

    deduplicated.sort(key=sort_key)

    # Step 3: Count unique records for logging
    seen_virtual_record_ids = set()
    for result in deduplicated:
        virtual_record_id = result.get("virtual_record_id")
        if not virtual_record_id:
            virtual_record_id = result.get("metadata", {}).get("virtualRecordId")
        if virtual_record_id:
            seen_virtual_record_ids.add(virtual_record_id)

    log.info(
        f"✅ Merged {len(deduplicated)} blocks from {len(seen_virtual_record_ids)} records "
        f"(deduplicated from {len(final_results)} raw results, sorted by relevance score). "
        f"Block numbering will be done by get_message_content()."
    )

    return deduplicated


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
        await _generate_direct_response(state, llm, log, writer, config)
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
    query = state.get("query", "")

    # ================================================================
    # FAST PATH: API-only results with sub-agent analyses
    # When there are no retrieval results (no citations needed) and
    # sub-agents already produced analyses, use a lightweight LLM call
    # instead of the full stream_llm_response_with_tools pipeline.
    # This typically saves 20-30 seconds by avoiding redundant processing.
    #
    # For complex tasks (weekly summaries, reports), sub_agent_analyses
    # contain consolidated domain summaries and tool_results may be empty
    # (raw results replaced by summaries). The fast-path still applies.
    # ================================================================
    sub_agent_analyses = state.get("sub_agent_analyses", [])

    # Rebuild from completed_tasks if sub_agent_analyses is empty (safety net)
    if not sub_agent_analyses:
        completed_tasks = state.get("completed_tasks", [])
        for ct in completed_tasks:
            if ct.get("status") != "success":
                continue
            ct_id = ct.get("task_id", "unknown")
            ct_domains = ", ".join(ct.get("domains", []))
            ds = ct.get("domain_summary")
            if ds:
                sub_agent_analyses.append(f"[{ct_id} ({ct_domains})]: {ds}")
                continue
            ct_result = ct.get("result", {})
            if isinstance(ct_result, dict):
                rt = ct_result.get("response", "")
                if rt:
                    sub_agent_analyses.append(f"[{ct_id} ({ct_domains})]: {rt}")
        if sub_agent_analyses:
            log.info("Rebuilt %d sub_agent_analyses from completed_tasks", len(sub_agent_analyses))

    log.info(
        "Fast-path check: analyses=%d, final_results=%d, virtual_map=%d, tool_results=%d",
        len(sub_agent_analyses), len(final_results), len(virtual_record_map), len(tool_results),
    )
    if (
        sub_agent_analyses
        and not final_results
        and not virtual_record_map
    ):
        log.info("⚡ Fast-path: API-only results with sub-agent analysis, using lightweight response")
        try:
            result = await _generate_fast_api_response(
                state, llm, query, tool_results, sub_agent_analyses, log, writer, config,
            )
            if result:
                duration_ms = (time.perf_counter() - start_time) * 1000
                log.info(f"⚡ respond_node (fast-path): {duration_ms:.0f}ms")
                return state
        except Exception as e:
            log.warning(f"Fast-path failed, falling back to standard: {e}")
            # Fall through to standard path

    # ================================================================
    # Merge and deduplicate retrieval results from parallel calls.
    # Then sort by (virtual_record_id, block_index) — the SAME ordering
    # the chatbot uses so that get_message_content() assigns consistent
    # R-labels (R1 = first record's blocks, R2 = second record, etc.)
    # ================================================================
    if final_results:
        final_results = merge_and_number_retrieval_results(final_results, log)
        # Mirror chatbot sort: group by record then by block order within record
        final_results = sorted(
            final_results,
            key=lambda x: (
                x.get("virtual_record_id") or x.get("metadata", {}).get("virtualRecordId", ""),
                x.get("block_index", 0),
            ),
        )
        state["final_results"] = final_results

    log.info(f"📚 Citation data: {len(final_results)} results, {len(virtual_record_map)} records")

    # ================================================================
    # Use get_message_content() — the EXACT same function the chatbot
    # uses — to build the user message with knowledge context.
    # This ensures:
    #   • Consistent R{record_number}-{block_index} block labels
    #   • The same rich context_metadata per record
    #   • The same tool instructions (fetch_full_record with R-labels)
    #   • The same output-format instructions
    # The formatted content is stored in state["qna_message_content"]
    # and consumed by create_response_messages() below.
    # ================================================================
    if final_results and virtual_record_map:
        from app.utils.chat_helpers import get_message_content as _get_msg_content

        # Build user_data string (same logic as chatbot's askAIStream)
        user_data = ""
        user_info = state.get("user_info") or {}
        org_info = state.get("org_info") or {}
        if user_info:
            account_type = (org_info.get("accountType") or "") if org_info else ""
            if account_type in ("Enterprise", "Business"):
                user_data = (
                    "I am the user of the organization. "
                    f"My name is {user_info.get('fullName', 'a user')} "
                    f"({user_info.get('designation', '')}) "
                    f"from {org_info.get('name', 'the organization')}. "
                    "Please provide accurate and relevant information."
                )
            else:
                user_data = (
                    "I am the user. "
                    f"My name is {user_info.get('fullName', 'a user')} "
                    f"({user_info.get('designation', '')}). "
                    "Please provide accurate and relevant information."
                )

        qna_content = _get_msg_content(
            final_results, virtual_record_map, user_data, query, log, "json"
        )
        state["qna_message_content"] = qna_content
        log.debug("✅ Built qna_message_content via get_message_content() (chatbot-identical format)")
    else:
        state["qna_message_content"] = None

    # Build R-label → virtual_record_id mapping AFTER sorting so the numbering
    # matches what get_message_content() assigned above.
    from app.modules.qna.response_prompt import build_record_label_mapping
    record_label_map: dict = build_record_label_mapping(final_results) if final_results else {}
    if record_label_map:
        log.debug(f"📌 Record label mapping: {record_label_map}")
    state["record_label_to_uuid_map"] = record_label_map

    # Build messages (create_response_messages uses qna_message_content as user msg)
    messages = create_response_messages(state)

    # Append non-retrieval tool results (API tools: Jira, Slack, etc.)
    # Retrieval results are already embedded in the user message via get_message_content().
    non_retrieval_results = [
        r for r in tool_results
        if r.get("status") == "success"
        and "retrieval" not in r.get("tool_name", "").lower()
    ]
    failed_results = [r for r in tool_results if r.get("status") == "error"]

    # Check for sub-agent analyses (deep agent: pre-analyzed domain summaries)
    # These are available even when tool_results is empty (complex tasks produce
    # domain summaries instead of raw tool results).
    _sub_analyses = sub_agent_analyses if sub_agent_analyses else state.get("sub_agent_analyses", [])
    has_api_results = non_retrieval_results or (failed_results and not any(r.get("status") == "success" for r in tool_results))

    if has_api_results or _sub_analyses:
        # Build context for API tool results.
        # When qna_message_content is set, retrieval blocks are already embedded in the
        # user message — pass [] to avoid duplication but set has_retrieval_in_context=True
        # so the LLM is instructed to use MODE 3 (inline citations + referenceData).
        qna_has_retrieval = bool(state.get("qna_message_content"))
        context = _build_tool_results_context(
            tool_results,
            [] if qna_has_retrieval else final_results,
            has_retrieval_in_context=qna_has_retrieval,
        ) if has_api_results else ""

        # Include sub-agent analyses (deep agent: pre-analyzed summaries)
        if _sub_analyses:
            analyses_text = "\n## Sub-Agent Analysis\n\n"
            analyses_text += (
                "The following detailed analysis was produced by specialized sub-agents "
                "that studied the raw data in depth. "
            )
            if has_api_results:
                analyses_text += (
                    "Preserve ALL items and findings from this analysis in your response. "
                    "Cross-reference with the raw API data above for exact details, links, "
                    "and any additional information.\n\n"
                )
            else:
                analyses_text += (
                    "Preserve ALL items and findings from this analysis in your response. "
                    "Include every data point, link, and detail.\n\n"
                )
            for analysis in _sub_analyses[:5]:
                analyses_text += f"{analysis}\n\n"
            context = analyses_text + context

        if context.strip():
            if messages and isinstance(messages[-1], HumanMessage):
                last_content = messages[-1].content
                if isinstance(last_content, list):
                    # qna_message_content is a list of content items — append as text item
                    last_content.append({"type": "text", "text": context})
                else:
                    messages[-1].content = last_content + context
            else:
                messages.append(HumanMessage(content=context))

    try:
        log.info("🎯 Using stream_llm_response_with_tools...")

        # Get required parameters from state
        retrieval_service = state.get("retrieval_service")
        user_id = state.get("user_id", "")
        org_id = state.get("org_id", "")
        graph_provider = state.get("graph_provider")
        is_multimodal_llm = state.get("is_multimodal_llm", False)

        # blob_store is not set by retrieval.py (which creates its own local instance);
        # create one here so stream_llm_response_with_tools can use it when the token
        # threshold is exceeded and a secondary retrieval is needed.
        blob_store = state.get("blob_store")
        if blob_store is None:
            try:
                from app.modules.transformers.blob_storage import BlobStorage
                config_svc = state.get("config_service")
                blob_store = BlobStorage(
                    logger=log,
                    config_service=config_svc,
                    graph_provider=graph_provider,
                )
                state["blob_store"] = blob_store
            except Exception as _bs_err:
                log.warning(f"Could not initialise BlobStorage in respond_node: {_bs_err}")

        # Get context_length from config or use default
        DEFAULT_CONTEXT_LENGTH = 128000
        config_service = state.get("config_service")
        context_length = DEFAULT_CONTEXT_LENGTH
        if config_service:
            with contextlib.suppress(Exception):
                # Try to get context length from LLM config if available
                # This is a fallback - ideally it should be stored in state
                context_length = DEFAULT_CONTEXT_LENGTH

        # Construct all_queries from state
        query = state.get("query", "")
        decomposed_queries = state.get("decomposed_queries", [])
        if decomposed_queries:
            all_queries = [q.get("query", query) for q in decomposed_queries if isinstance(q, dict) and q.get("query")]
            if not all_queries:
                all_queries = [query]
        else:
            all_queries = [query]

        # Create the agent-specific fetch_full_record tool (mirrors the chatbot
        # pipeline: returns raw record dicts so execute_tool_calls in streaming.py
        # formats them via record_to_message_content() — identical to chatbot).
        tools = []
        if virtual_record_map:
            from app.utils.agent_fetch_full_record import (
                create_agent_fetch_full_record_tool,
            )
            fetch_tool = create_agent_fetch_full_record_tool(
                virtual_record_map,
                label_to_virtual_record_id=record_label_map if record_label_map else None,
            )
            tools = [fetch_tool]
            log.debug(
                f"Added agent fetch_full_record tool "
                f"({len(virtual_record_map)} records available, "
                f"{len(record_label_map)} labels mapped)"
            )

        # Create tool_runtime_kwargs
        tool_runtime_kwargs = {
            "blob_store": blob_store,
            "graph_provider": graph_provider,
            "org_id": org_id,
            "conversation_id": state.get("conversation_id"),
        }

        answer_text = ""
        citations = []
        reason = None
        confidence = None
        reference_data = []

        async for stream_event in stream_llm_response_with_tools(
            llm=llm,
            messages=messages,
            final_results=final_results,
            all_queries=all_queries,
            retrieval_service=retrieval_service,
            user_id=user_id,
            org_id=org_id,
            virtual_record_id_to_result=virtual_record_map,
            blob_store=blob_store,
            is_multimodal_llm=is_multimodal_llm,
            context_length=context_length,
            tools=tools,
            tool_runtime_kwargs=tool_runtime_kwargs,
            target_words_per_chunk=1,
            mode="json",
            is_agent=True,  # Use agent schemas (with referenceData support)
            conversation_id=state.get("conversation_id"),
        ):
            event_type = stream_event.get("event")
            event_data = stream_event.get("data", {})

            # ── Agent-side citation enrichment (no streaming.py changes) ────────
            # streaming.py's normalize_citations_and_chunks extracts citations from
            # inline [R#-#] markers in the LLM answer text.  In combined (MODE 3)
            # responses the LLM may skip inline markers and rely only on blockNumbers.
            # streaming.py does not forward blockNumbers in the complete event, so we
            # apply a second-pass extraction here — before the event reaches the client.
            #
            # Pass 1: re-run inline marker extraction in case streaming.py received
            #         stale/empty final_results (safety net for edge cases).
            # Pass 2: if the LLM DID write inline markers they are already extracted
            #         by streaming.py; citations will be non-empty and we skip below.
            if (
                event_type == "complete"
                and final_results
                and not event_data.get("citations")
            ):
                _raw_answer = event_data.get("answer", "")
                _enriched: list = []
                if _raw_answer:
                    try:
                        from app.utils.citations import (
                            normalize_citations_and_chunks_for_agent as _ncc_agent,
                        )
                        _, _enriched = _ncc_agent(_raw_answer, final_results, virtual_record_map, [])
                        if _enriched:
                            log.info(
                                "🔖 Citation enrichment (respond_node): "
                                "extracted %d citations from inline markers",
                                len(_enriched),
                            )
                    except Exception as _ce:
                        log.debug("Citation enrichment error: %s", _ce)
                if _enriched:
                    # Shallow-copy so we never mutate the yielded stream_event dict
                    event_data = {**event_data, "citations": _enriched}
            # ────────────────────────────────────────────────────────────────────

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
    llm: BaseChatModel,
    log: logging.Logger,
    writer: StreamWriter,
    config: RunnableConfig,
) -> None:
    """Generate direct response with full conversation context.

    Streams the LLM response to the frontend, sends the completion event,
    and stores the result in state. Fully self-contained — the caller just
    needs to ``return state`` after this returns.
    """
    from app.utils.streaming import stream_llm_response

    query = state.get("query", "")
    previous = state.get("previous_conversations", [])

    # Build messages with full conversation history (same as planner)
    messages = []

    # System message
    user_context = _format_user_context(state)

    # Build instructions prefix if agent has configured instructions
    instructions_prefix = ""
    agent_instructions = state.get("instructions")
    if agent_instructions and agent_instructions.strip():
        instructions_prefix = f"## Agent Instructions\n{agent_instructions.strip()}\n\n"

    base_system_prompt = state.get("system_prompt", "")
    role_prefix = ""
    if base_system_prompt and base_system_prompt.strip() and base_system_prompt != "You are an enterprise questions answering expert":
        role_prefix = f"{base_system_prompt.strip()}\n\n"

    # If the agent has no knowledge and no tools, use a specialized system prompt that
    # always steers the LLM to guide the user to configure the agent for org-specific queries.
    if state.get("agent_not_configured_hint"):
        system_content = (
            f"{instructions_prefix}{role_prefix}"
            "You are a helpful, friendly AI assistant.\n\n"
            "## ⚠️ IMPORTANT: This Agent Is Not Configured\n"
            "This agent has **no knowledge sources** and **no service tools** connected.\n\n"
            "### How to respond:\n"
            "1. **NEVER leak internal terms** like `can_answer_directly`, `needs_clarification`, JSON keys, or planning details into your response. Write naturally.\n"
            "2. **For greetings, math, or clearly general questions**: answer normally and briefly.\n"
            "3. **For ANY question about org-specific data** (licenses, documents, policies, project details, account info, internal systems, product-specific questions, etc.):\n"
            "   - Do NOT speculate, fabricate, or invent org-specific details.\n"
            "   - Do NOT make up license details, account information, or product internals you have no real knowledge of.\n"
            "   - Clearly tell the user: **this agent has no knowledge sources configured**, so you cannot look up their specific information.\n"
            "   - Then guide them: to get accurate answers about their org's data, the agent admin needs to:\n"
            "     * **Add Knowledge Sources** — upload documents, wikis, or connect data sources to this agent.\n"
            "     * **Connect Service Toolsets** — link apps (Google Workspace, Jira, Confluence, Slack, etc.) to enable live actions.\n"
            "   - You may add a brief general explanation from your training knowledge if helpful, but **always make clear it is general knowledge, not their specific data**.\n"
            "4. **Keep responses concise and user-friendly** — the user may not know the agent needs to be set up."
        )
        if user_context:
            system_content += "\n\nIMPORTANT: When user asks about themselves, use provided info DIRECTLY."
    else:
        system_content = f"{instructions_prefix}{role_prefix}You are a helpful, friendly AI assistant. Respond naturally and concisely."
        if user_context:
            system_content += "\n\nIMPORTANT: When user asks about themselves, use provided info DIRECTLY."

    # Add capability summary so direct responses can answer "what can you do?"
    capability_summary = build_capability_summary(state)
    system_content += f"\n\n{capability_summary}"

    messages.append(SystemMessage(content=system_content))

    # Add conversation history as LangChain messages (with sliding window)
    if previous:
        conversation_messages = _build_conversation_messages(previous, log)
        messages.extend(conversation_messages)
        log.debug(f"Using {len(conversation_messages)} messages from {len(previous)} conversations for direct response (sliding window applied)")

    # Current query
    user_content = query
    if user_context:
        user_content += f"\n\n{user_context}"
    messages.append(HumanMessage(content=user_content))

    answer_text = ""
    citations: list = []

    try:
        async for stream_event in stream_llm_response(
            llm=llm,
            messages=messages,
            final_results=[],
            logger=log,
            target_words_per_chunk=1,
            mode="simple",
        ):
            event_type = stream_event.get("event")
            event_data = stream_event.get("data", {})

            safe_stream_write(writer, {"event": event_type, "data": event_data}, config)

            if event_type == "complete":
                answer_text = event_data.get("answer", "")
                citations = event_data.get("citations", [])

    except Exception as e:
        log.error("Direct response failed: %s", e, exc_info=True)
        answer_text = "I'm here to help! How can I assist you today?"
        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {"chunk": answer_text, "accumulated": answer_text, "citations": []},
        }, config)
        safe_stream_write(writer, {
            "event": "complete",
            "data": {"answer": answer_text, "citations": [], "confidence": "Low"},
        }, config)

    answer = answer_text.strip() or "I'm here to help! How can I assist you today?"
    state["response"] = answer
    state["completion_data"] = {
        "answer": answer,
        "citations": citations,
        "confidence": "High",
        "answerMatchType": "Direct Response",
    }


async def _generate_fast_api_response(
    state: ChatState,
    llm: BaseChatModel,
    query: str,
    tool_results: list[dict],
    sub_agent_analyses: list[str],
    log: logging.Logger,
    writer: StreamWriter,
    config: RunnableConfig,
) -> bool:
    """
    Fast-path response for API-only results with sub-agent analyses.

    Instead of the full stream_llm_response_with_tools pipeline (which includes
    tool execution capability, citation extraction, etc.), this uses a lightweight
    streaming LLM call that takes the sub-agent's pre-analyzed data and formats
    it as the final response.

    Returns True if response was generated successfully, False to fall back.
    """
    # Build a concise prompt with the sub-agent analysis and raw data
    analyses_text = "\n\n".join(sub_agent_analyses)

    # Include raw API data for reference
    non_retrieval = [
        r for r in tool_results
        if r.get("status") == "success"
        and "retrieval" not in r.get("tool_name", "").lower()
    ]
    raw_data_parts = []
    for r in non_retrieval[:5]:
        tool_name = r.get("tool_name", "unknown")
        content = ToolResultExtractor.extract_data_from_result(r.get("result", ""))
        if isinstance(content, (dict, list)):
            content_str = json.dumps(content, indent=2, default=str)
        else:
            content_str = str(content)
        # Limit raw data size to keep the prompt small
        if len(content_str) > _RAW_DATA_SIZE_LIMIT:
            content_str = content_str[:_RAW_DATA_SIZE_LIMIT] + "\n... (truncated)"
        raw_data_parts.append(f"### {tool_name}\n```json\n{content_str}\n```")

    raw_data_text = "\n\n".join(raw_data_parts) if raw_data_parts else ""

    # Build instructions prefix if agent has configured instructions
    instructions_prefix = ""
    agent_instructions = state.get("instructions")
    if agent_instructions and agent_instructions.strip():
        instructions_prefix = f"## Agent Instructions\n{agent_instructions.strip()}\n\n"

    base_system_prompt = state.get("system_prompt", "")
    role_prefix = ""
    if base_system_prompt and base_system_prompt.strip() and base_system_prompt != "You are an enterprise questions answering expert":
        role_prefix = f"{base_system_prompt.strip()}\n\n"

    system_prompt = (
        f"{instructions_prefix}{role_prefix}"
        "You are an expert data analyst producing comprehensive, detailed reports.\n\n"
        "You will receive:\n"
        "1. **Sub-Agent Analysis** — pre-analyzed, structured findings from specialized agents "
        "that have already studied the raw data in depth\n"
        "2. **Raw API Data** — the original data for cross-referencing and extracting additional "
        "details (links, exact values) that the analysis may reference\n\n"
        "## Objective\n"
        "Produce a thorough, detailed response — NOT a brief summary. The user expects "
        "deep analysis with every relevant data point preserved.\n\n"
        "## Quality Standards\n"
        "- **Comprehensive**: Include EVERY item, finding, and data point from the analysis. "
        "Do not drop, skip, or summarize away any items\n"
        "- **Accurate**: Cross-reference the sub-agent analysis with raw API data to verify "
        "details and extract additional information (exact timestamps, IDs, URLs, emails)\n"
        "- **Specific**: Use exact values — dates, times, names, emails, statuses, priorities, "
        "counts. Never use vague quantifiers ('several', 'multiple', 'some') when exact "
        "counts are available\n"
        "- **Linked**: Include ALL clickable URLs found in BOTH the analysis AND raw data. "
        "Format as `[Title](url)`. Links are mandatory for every item that has one\n"
        "- **Well-structured**: Use tables for list data, headers for sections, bullet points "
        "for details. Choose the format that best presents each type of data\n"
        "- **Actionable**: Surface critical items prominently (overdue, high-priority, errors, "
        "action required). Include follow-ups and recommendations where supported by data\n"
        "- **No fabrication**: Only use data that is explicitly provided\n"
        "- Output ONLY markdown — no JSON wrapper, no code fences around the whole response\n"
    )

    user_content = f"**User Query**: {query}\n\n"
    user_content += f"## Sub-Agent Analysis\n{analyses_text}\n\n"
    if raw_data_text:
        user_content += (
            f"## Raw API Data (for cross-referencing and extracting exact details)\n"
            f"{raw_data_text}\n\n"
        )
    user_content += (
        "Produce a comprehensive, detailed markdown response. Preserve ALL items and data points "
        "from the analysis. Cross-reference with raw data for accuracy and links. Do NOT wrap in JSON."
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]

    full_content = ""
    reference_data = []

    try:
        async for stream_event in stream_llm_response(
            llm=llm,
            messages=messages,
            final_results=[],
            logger=log,
            target_words_per_chunk=1,
            mode="simple",
        ):
            event_type = stream_event.get("event")
            event_data = stream_event.get("data", {})

            if event_type == "complete":
                # Capture the full answer but don't forward — we emit our own complete event below
                full_content = event_data.get("answer", "")
            else:
                safe_stream_write(writer, {"event": event_type, "data": event_data}, config)

    except Exception as e:
        log.error("Fast API response generation failed: %s", e, exc_info=True)
        full_content = ""

    if not full_content.strip():
        return False

    conversation_id = state.get("conversation_id")
    if conversation_id:
        try:
            from app.utils.conversation_tasks import await_and_collect_results
            for task_result in await await_and_collect_results(conversation_id):
                safe_stream_write(writer, {"event": "conversation_task", "data": task_result}, config)
        except Exception as e:
            log.warning("Fast-path: conversation tasks failed: %s", e)

    # Extract reference data (links) from the raw tool results
    reference_data = []
    for r in non_retrieval:
        content = r.get("result", "")
        _extract_urls_for_reference_data(content, reference_data)

    answer_text = full_content.strip()
    conversation_id = state.get("conversation_id")
    if conversation_id:
        try:
            from app.utils.conversation_tasks import await_and_collect_results
            from app.utils.streaming import _append_task_markers
            task_results = await await_and_collect_results(conversation_id)
            answer_text = _append_task_markers(answer_text, task_results)
        except Exception as e:
            log.warning("Fast-path: conversation tasks failed: %s", e)

    completion_data = {
        "answer": answer_text,
        "citations": [],
        "confidence": "High",
        "answerMatchType": "Derived From Tool Execution",
    }
    if reference_data:
        completion_data["referenceData"] = reference_data

    safe_stream_write(writer, {"event": "complete", "data": completion_data}, config)
    state["response"] = answer_text
    state["completion_data"] = completion_data
    return True


def _extract_urls_for_reference_data(content: object, reference_data: list[dict]) -> None:
    """Extract URLs from tool result content and add to referenceData list."""
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return

    if isinstance(content, dict):
        for key, value in content.items():
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                # Found a URL — add to reference data if not already present
                if not any(rd.get("url") == value for rd in reference_data):
                    name = content.get("subject") or content.get("title") or content.get("name") or content.get("key") or key
                    reference_data.append({"name": str(name), "url": value, "type": key})
            elif isinstance(value, (dict, list)):
                _extract_urls_for_reference_data(value, reference_data)
    elif isinstance(content, list):
        for item in content[:20]:  # Safety limit
            _extract_urls_for_reference_data(item, reference_data)


def _build_tool_results_context(
    tool_results: list[dict],
    final_results: list[dict],
    *,
    has_retrieval_in_context: bool = False,
) -> str:
    """Build context from tool results for response generation.

    Args:
        tool_results: All tool results (success + error) from this cycle.
        final_results: Retrieval results already embedded in qna_message_content.
                       Pass [] when they are already in qna_message_content to avoid
                       duplication; use has_retrieval_in_context=True instead to signal
                       that retrieval knowledge IS present in the conversation context.
        has_retrieval_in_context: True when retrieval knowledge blocks are already in
                       the user message (qna_message_content). This tells the LLM to
                       use MODE 3 (combined citations + referenceData) even though the
                       blocks aren't repeated in this tool-results section.
    """
    successful = [r for r in tool_results if r.get("status") == "success"]
    failed = [r for r in tool_results if r.get("status") == "error"]
    # has_retrieval is True when blocks are in final_results OR already in context
    has_retrieval = bool(final_results) or has_retrieval_in_context
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
        # When blocks come from final_results, show count. When they're already in
        # qna_message_content (has_retrieval_in_context=True), just remind the LLM.
        if final_results:
            parts.append("\n## 📚 Internal Knowledge Available\n\n")
            parts.append(f"You have {len(final_results)} knowledge blocks.\n")
        else:
            parts.append("\n## 📚 Internal Knowledge in Context\n\n")
            parts.append(
                "Internal knowledge blocks (with R-labels like R1-0, R2-3) are present "
                "in the conversation above.\n"
            )
        parts.append(
            "**MANDATORY**: Cite IMMEDIATELY after each fact from internal knowledge: [R1-0], [R2-3]\n"
            "Include ALL cited block labels in `blockNumbers`.\n\n"
        )

    if non_retrieval:
        parts.append("\n## 🔧 API Tool Results\n\n")
        parts.append(
            "Transform raw data into professional, informative markdown. Follow these rules:\n"
            "- **Be specific**: Show exact values (dates, times, names, emails, statuses) — never summarize vaguely.\n"
            "- **Include links**: Extract ALL URL fields from the data and render as clickable markdown links.\n"
            "- **People fields**: Show names WITH email addresses when available: `Name (email@example.com)`.\n"
            "- **For single items**: Show all relevant fields as detailed field-value pairs.\n"
            "- **For lists**: Use clean, scannable markdown tables with the most important fields. "
            "Prioritize user-actionable, business-relevant data. Include custom fields that have values.\n"
            "- **Exclude**: Internal system metadata, aggregate calculations, empty/null fields, technical IDs "
            "that aren't user-facing. Show user-facing IDs/keys, hide internal ones.\n"
            "- Store all IDs, keys, and links in referenceData.\n\n"
        )

        for r in non_retrieval:
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
        parts.append(
            "**⚠️ MODE 3 — COMBINED RESPONSE (MANDATORY)**\n"
            "You have BOTH internal knowledge blocks (R-labels in context) AND API tool results.\n"
            "This is the MOST ACCURATE mode — you have both indexed historical content AND live current data.\n"
            "You MUST:\n"
            "  1. Synthesize BOTH sources into ONE coherent, comprehensive answer\n"
            "  2. Use retrieval results for historical context, background, and comprehensive coverage\n"
            "  3. Use API results for current state, real-time data, and exact IDs/keys\n"
            "  4. When sources conflict, prioritize API results for current state, but mention historical context from retrieval\n"
            "  5. Cite every fact from internal knowledge inline: [R1-0], [R2-3]\n"
            "  6. Include all cited labels in `blockNumbers`\n"
            "  7. Format all API items as clickable links and include them in `referenceData`\n"
            "  8. Combine insights: \"Based on our indexed knowledge [R1-0], and current live data, here's the complete picture...\"\n\n"
        )
    elif has_retrieval:
        parts.append(
            "**INTERNAL KNOWLEDGE**: Use knowledge blocks with inline citations [R1-0].\n"
            "Include all cited labels in `blockNumbers`.\n"
        )
    else:
        parts.append(
            "**API DATA**: Transform into professional markdown. "
            "Show user-facing IDs (keys), hide internal IDs.\n"
        )

    if len(non_retrieval) > 1:
        parts.append(
            "\n**IMPORTANT**: You have results from MULTIPLE tools. "
            "Merge and present results from ALL tools — do NOT ignore any tool's output. "
            "Deduplicate overlapping items but ensure every unique result is included.\n"
        )

    parts.append(
        "\n## 🔗 LINK REQUIREMENTS (MANDATORY)\n\n"
        "For EVERY item from ANY external service, you MUST include a clickable markdown link.\n"
        "**How to find links**: Scan ALL fields in the raw JSON for values starting with `http://` or `https://`. "
        "Common URL field names: `url`, `webLink`, `webViewLink`, `self`, `htmlUrl`, `permalink`, "
        "`link`, `href`, `joinUrl`, `joinWebUrl` — but ANY field containing a URL should be used.\n"
        "**Format**: `[Item Title or Name](url_value)` — always use the item's title/name/subject/key as the link text.\n"
        "**If no URL found**: Still mention the item by name/key/ID so the user can locate it.\n"
        "**referenceData**: Include ALL discovered links in the referenceData array: "
        "`{\"name\": \"<item title>\", \"url\": \"<url value>\", \"type\": \"<service_type>\"}`.\n\n"
    )

    # The JSON schema returned depends on what sources are present
    if has_retrieval and non_retrieval:
        parts.append(
            "Return ONLY JSON matching MODE 3:\n"
            "{\"answer\": \"...with inline [R1-0] citations...\", "
            "\"confidence\": \"High\", "
            "\"answerMatchType\": \"Derived From Blocks\", "
            "\"blockNumbers\": [\"R1-0\", \"R2-3\"], "
            "\"referenceData\": [{\"name\": \"...\", \"key\": \"...\", \"type\": \"...\", \"url\": \"...\"}]}\n"
        )
    elif has_retrieval:
        parts.append(
            "Return ONLY JSON:\n"
            "{\"answer\": \"...with inline [R1-0] citations...\", "
            "\"confidence\": \"High\", "
            "\"answerMatchType\": \"Derived From Blocks\", "
            "\"blockNumbers\": [\"R1-0\", \"R2-3\"]}\n"
        )
    else:
        parts.append(
            "Return ONLY JSON:\n"
            "{\"answer\": \"...\", \"confidence\": \"High\", "
            "\"answerMatchType\": \"Derived From Tool Execution\", "
            "\"referenceData\": [{\"name\": \"...\", \"key\": \"...\", \"type\": \"...\", \"url\": \"...\"}]}\n"
        )

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
    """Process retrieval tool output (accumulates results from multiple retrieval calls)"""
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
            # Accumulate final_results instead of overwriting (for parallel retrieval calls)
            existing_final_results = state.get("final_results", [])
            if not isinstance(existing_final_results, list):
                existing_final_results = []

            # Combine new results with existing ones
            new_final_results = retrieval_output.final_results or []
            combined_final_results = existing_final_results + new_final_results
            state["final_results"] = combined_final_results

            # Accumulate virtual_record_id_to_result
            existing_virtual_map = state.get("virtual_record_id_to_result", {})
            if not isinstance(existing_virtual_map, dict):
                existing_virtual_map = {}

            new_virtual_map = retrieval_output.virtual_record_id_to_result or {}
            combined_virtual_map = {**existing_virtual_map, **new_virtual_map}
            state["virtual_record_id_to_result"] = combined_virtual_map

            # Accumulate tool_records
            if retrieval_output.virtual_record_id_to_result:
                existing_tool_records = state.get("tool_records", [])
                if not isinstance(existing_tool_records, list):
                    existing_tool_records = []

                new_tool_records = list(retrieval_output.virtual_record_id_to_result.values())
                # Avoid duplicates by checking record IDs
                existing_record_ids = {rec.get("_id") for rec in existing_tool_records if isinstance(rec, dict) and "_id" in rec}
                unique_new_records = [
                    rec for rec in new_tool_records
                    if not (isinstance(rec, dict) and rec.get("_id") in existing_record_ids)
                ]
                combined_tool_records = existing_tool_records + unique_new_records
                state["tool_records"] = combined_tool_records

            log.info(f"Retrieved {len(new_final_results)} knowledge blocks (total: {len(combined_final_results)})")
            return retrieval_output.content

    except Exception as e:
        log.warning(f"Could not process retrieval output: {e}")

    return str(result)


def _detect_tool_result_status(result_content: object) -> str:
    """
    Detect whether a tool result indicates success or error.

    Parses the tool result content (string or dict) and checks for
    error indicators. Returns "success" or "error".
    """
    try:
        # Parse JSON string to dict if needed
        parsed = result_content
        if isinstance(result_content, str):
            try:
                parsed = json.loads(result_content)
            except (json.JSONDecodeError, ValueError):
                # Not JSON — check for error keywords in raw string
                lower_content = result_content.lower()[:500]
                if any(marker in lower_content for marker in [
                    "error executing tool",
                    '"status": "error"',
                    "authentication failed",
                    "permission denied",
                    "unauthorized",
                    "403 forbidden",
                    "404 not found",
                    "500 internal server error",
                ]):
                    return "error"
                return "success"

        # Check dict-style results
        if isinstance(parsed, dict):
            # Check explicit status field
            status = parsed.get("status", "")
            if isinstance(status, str) and status.lower() == "error":
                return "error"

            # Check for error key
            if parsed.get("error"):
                return "error"

            # Check for success=False pattern (tuple-style result)
            if parsed.get("success") is False:
                return "error"

        # Check tuple-style: (False, "error message")
        if isinstance(parsed, (list, tuple)) and len(parsed) >= TOOL_RESULT_TUPLE_LENGTH and parsed[0] is False:
            return "error"

    except Exception:
        pass  # If we can't parse it, assume success

    return "success"


# =============================================================================
# ReAct Agent Streaming Callback
# =============================================================================

class _ToolStreamingCallback(AsyncCallbackHandler):
    """
    Callback handler that streams tool execution events to the frontend
    via the outer graph's StreamWriter.

    Used with agent.ainvoke() to stream real-time tool status without
    the context conflicts that occur with nested agent.astream() calls.
    """

    def __init__(self, writer: StreamWriter, config: RunnableConfig, log: logging.Logger) -> None:
        super().__init__()
        self.writer = writer
        self.config = config
        self.log = log
        self._tool_names: dict[str, str] = {}  # run_id -> tool_name

    def _write_event(self, event_data: dict[str, Any]) -> bool:
        """Write event to the outer graph's stream with context restoration."""
        token = var_child_runnable_config.set(self.config)
        try:
            self.writer(event_data)
            return True
        except Exception as e:
            self.log.debug(f"Stream callback write failed: {e}")
            return False
        finally:
            var_child_runnable_config.reset(token)

    async def on_tool_start(
        self, serialized: dict[str, Any], input_str: str, *, run_id: UUID, **kwargs: object
    ) -> None:
        tool_name = serialized.get("name", kwargs.get("name", "unknown"))
        self._tool_names[str(run_id)] = tool_name
        status_msg = _get_tool_status_message(tool_name)
        self.log.info(f"Streaming tool start: {tool_name} -> {status_msg}")
        self._write_event({
            "event": "status",
            "data": {"status": "executing", "message": status_msg}
        })

    async def on_tool_end(self, output: object, *, run_id: UUID, **kwargs: object) -> None:
        tool_name = self._tool_names.pop(str(run_id), kwargs.get("name", "unknown"))
        tool_status = _detect_tool_result_status(output)
        result_preview = str(output)[:MAX_TOOL_RESULT_PREVIEW_LENGTH]
        self.log.info(f"Streaming tool end: {tool_name} -> {tool_status}")

        if tool_status == "error":
            action_readable = tool_name.split(".", 1)[-1].replace("_", " ")
            self._write_event({
                "event": "status",
                "data": {
                    "status": "executing",
                    "message": f"Retrying {action_readable} with corrected parameters..."
                }
            })

        self._write_event({
            "event": "tool_result",
            "data": {
                "tool": tool_name,
                "result": result_preview,
                "status": tool_status,
            }
        })

    async def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: object) -> None:
        tool_name = self._tool_names.pop(str(run_id), kwargs.get("name", "unknown"))
        error_msg = str(error)[:200]
        self.log.info(f"Streaming tool error: {tool_name} -> {error_msg}")
        self._write_event({
            "event": "status",
            "data": {
                "status": "executing",
                "message": f"Error in {tool_name.replace('_', ' ')}: {error_msg}. Retrying..."
            }
        })


async def react_agent_node(
    state: ChatState,
    config: RunnableConfig,
    writer: StreamWriter
) -> ChatState:
    """
    ReAct agent node with cascading tool execution support.

    This node uses LangChain's create_agent which naturally handles
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
        from langchain_core.messages import ToolMessage

        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

        safe_stream_write(writer, {
            "event": "status",
            "data": {"status": "planning", "message": "Analyzing your request and planning actions..."}
        }, config)

        # Get tools with Pydantic schemas
        tools = get_agent_tools_with_schemas(state)
        log.info(f"ReAct agent loaded {len(tools)} tools with schemas")

        # Stream tool count info
        tool_names_for_log = [getattr(t, 'name', str(t)) for t in tools[:_TOOL_LOG_LIMIT]]
        log.info(f"Available tools: {tool_names_for_log}{'...' if len(tools) > _TOOL_LOG_LIMIT else ''}")

        # Build system prompt
        system_prompt = _build_react_system_prompt(state, log)

        # ReAct agent via langchain.agents (create_react_agent moved here from langgraph.prebuilt)
        from langchain.agents import create_agent

        agent = create_agent(
            llm,
            tools,
            system_prompt=system_prompt,
        )

        # Build message history with conversation context (same as planner path).
        # This is critical for follow-ups like "yes execute" where parameters were
        # provided in previous turns.
        messages = _build_planner_messages(state, query, log)

        # Execute agent with callback-based streaming.
        # We use ainvoke() + AsyncCallbackHandler instead of astream() because
        # astream() creates a nested LangGraph execution context that conflicts
        # with the outer graph's StreamWriter, preventing tool status events from
        # reaching the frontend SSE stream. Callbacks are invoked during tool
        # execution and are orthogonal to the graph streaming mechanism.
        tool_results = []

        # Create streaming callback that writes tool events to the outer stream
        streaming_cb = _ToolStreamingCallback(writer, config, log)

        # Use a clean config for the inner agent to avoid context conflicts
        # with the outer graph. Only pass recursion_limit and callbacks.
        react_callbacks = [streaming_cb]
        if _opik_tracer:
            react_callbacks.append(_opik_tracer)
        agent_config = {
            "recursion_limit": 50,
            "callbacks": react_callbacks,
        }

        keepalive_task = asyncio.create_task(
            send_keepalive(writer, config, "Processing with tools...")
        )
        try:
            result = await agent.ainvoke(
                {"messages": messages},
                config=agent_config,
            )
        finally:
            keepalive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keepalive_task

        # Extract messages from the agent result
        final_messages = result.get("messages", [])
        log.debug(f"ReAct agent returned {len(final_messages)} messages")

        # Process tool results from final messages
        for msg in final_messages:
            if isinstance(msg, ToolMessage):
                tool_name = msg.name if hasattr(msg, 'name') else "unknown"
                result_content = msg.content

                # Process retrieval tool results to extract final_results
                if "retrieval" in tool_name.lower():
                    try:
                        if isinstance(result_content, str):
                            parsed = json.loads(result_content)
                            _process_retrieval_output(parsed, state, log)
                        elif isinstance(result_content, dict):
                            _process_retrieval_output(result_content, state, log)
                    except Exception as e:
                        log.warning(f"Failed to process retrieval output: {e}")
                        _process_retrieval_output(result_content, state, log)

                # Detect actual tool success/failure from result content
                tool_status = _detect_tool_result_status(result_content)
                log.info("📌 ReAct tool status: %s | status=%s", tool_name, tool_status)

                tool_results.append({
                    "tool_name": tool_name,
                    "status": tool_status,
                    "result": result_content,
                    "tool_call_id": getattr(msg, 'tool_call_id', None),
                })

        # Stream analyzing status before response generation
        if tool_results:
            safe_stream_write(writer, {
                "event": "status",
                "data": {"status": "analyzing", "message": "Analyzing results and preparing response..."}
            }, config)

        # Get retrieval results (internal knowledge) - may have been populated by retrieval tool
        final_results = state.get("final_results", [])
        has_retrieval = bool(final_results)

        # Extract final response from messages
        response = _extract_final_response(final_messages, log)

        # Determine reflection decision based on tool results
        error_count = sum(1 for r in tool_results if r.get("status") == "error")
        success_count = sum(1 for r in tool_results if r.get("status") == "success")
        total_tools = len(tool_results)

        if total_tools == 0:
            # No tools called — direct answer
            reflection_decision = "respond_success"
            reflection_reasoning = "ReAct agent answered directly without tool calls."
        elif error_count == 0:
            reflection_decision = "respond_success"
            reflection_reasoning = f"All {success_count} tool call(s) succeeded."
        elif success_count > 0:
            reflection_decision = "respond_success"
            reflection_reasoning = f"{success_count}/{total_tools} tool calls succeeded, {error_count} failed. Partial results available."
        else:
            reflection_decision = "respond_error"
            reflection_reasoning = f"All {error_count} tool call(s) failed."

        # Hand off final formatting to respond_node so output matches chatbot format.
        # react_agent_node only performs tool orchestration and state preparation.
        state["response"] = response
        state["tool_results"] = tool_results
        state["all_tool_results"] = tool_results
        state["reflection_decision"] = reflection_decision
        state["reflection"] = {
            "decision": reflection_decision,
            "confidence": "High" if error_count == 0 else "Medium",
            "reasoning": reflection_reasoning,
        }

        execution_plan = state.get("execution_plan") or {}
        # When react agent answered directly without calling any tools (e.g.
        # capability questions, greetings), let respond_node use the
        # _generate_direct_response path which has the capability summary and
        # user context.  When tools WERE called, keep can_answer_directly=False
        # so the full synthesis pipeline runs with citations and tool results.
        execution_plan["can_answer_directly"] = (total_tools == 0)
        state["execution_plan"] = execution_plan

        duration_ms = (time.perf_counter() - start_time) * 1000
        log.info(
            f"⚡ ReAct Agent: {duration_ms:.0f}ms, {total_tools} tool calls "
            f"({success_count} success, {error_count} errors)"
        )
        log.info(
            "ReAct handoff -> respond_node | decision=%s | can_answer_directly=%s | has_retrieval=%s | response_len=%d",
            reflection_decision,
            execution_plan["can_answer_directly"],
            has_retrieval,
            len(response or ""),
        )

    except ImportError as e:
        log.error(f"ReAct agent dependencies not available: {e}")
        state["error"] = {
            "status": "error",
            "message": "ReAct agent is not available. Please use the standard agent.",
            "status_code": 500,
        }
    except Exception as e:
        log.error(f"ReAct agent error: {e}", exc_info=True)
        state["error"] = {
            "status": "error",
            "message": f"I encountered an error: {str(e)}",
            "status_code": 500,
        }

    return state


def _build_tool_schema_reference(state: ChatState, log: logging.Logger) -> str:
    """
    Build a concise tool schema reference for inclusion in the ReAct system prompt.

    This gives the LLM visibility into required vs optional parameters directly
    in the prompt text, enabling chain-of-thought reasoning about parameter
    validation before tool calls.
    """
    try:
        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

        tools = get_agent_tools_with_schemas(state)
        if not tools:
            return ""

        lines = ["## Tool Schema Quick Reference\n"]
        lines.append("Use this reference to validate parameters before every tool call.\n")

        for tool in tools[:30]:  # Limit to prevent prompt bloat
            name = getattr(tool, 'name', str(tool))
            lines.append(f"### {name}")

            schema = getattr(tool, 'args_schema', None)
            if schema:
                params_info = _extract_parameters_from_schema(schema, log)
                if params_info:
                    required_parts = []
                    optional_parts = []
                    for pname, pinfo in params_info.items():
                        ptype = pinfo.get("type", "any")
                        desc = pinfo.get("description", "")
                        short_desc = (desc[:_PARAM_DESC_TRUNCATE] + "...") if len(desc) > _PARAM_DESC_TRUNCATE else desc
                        entry = f"`{pname}` ({ptype})"
                        if short_desc:
                            entry += f": {short_desc}"
                        if pinfo.get("required"):
                            required_parts.append(entry)
                        else:
                            optional_parts.append(entry)

                    if required_parts:
                        lines.append("  **Required**: " + "; ".join(required_parts))
                    if optional_parts:
                        lines.append("  **Optional**: " + "; ".join(optional_parts))
                else:
                    lines.append("  (no parameters)")
            else:
                lines.append("  (no schema available)")
            lines.append("")

        return "\n".join(lines)

    except Exception as e:
        log.warning(f"Failed to build tool schema reference: {e}")
        return ""


def _build_workflow_patterns(state: ChatState) -> str:
    """
    Build multi-step workflow patterns based on available toolsets.

    Returns cross-service patterns only when the relevant toolsets are both
    active, so the prompt stays lean for single-service agents.
    """
    patterns = []

    has_outlook = _has_outlook_tools(state)
    has_confluence = _has_confluence_tools(state)
    has_slack = _has_slack_tools(state)
    has_teams = _has_teams_tools(state)

    if has_outlook and has_confluence:
        patterns.append("""### Cross-Service Pattern: Recurring Meetings + Holiday Exclusions

When user asks to extend/create recurring meetings and skip holidays/weekends:

**Step 1 — Find or create the recurring event:**
- For EXTEND: Use `outlook.get_recurring_events_ending` or `outlook.search_calendar_events_in_range`
  to find the event. Extract the `seriesMasterId` (or `id` if type is `seriesMaster`).
  Check conversation history first — if the event was recently displayed, use that data directly.
- For CREATE: Use `outlook.create_calendar_event` with the recurrence pattern.

**Step 2 — Update recurrence end date (extend only):**
`outlook.update_calendar_event(event_id=<seriesMasterId>, recurrence={pattern: <keep_existing>, range: {type: "endDate", startDate: "<original_start>", endDate: "<new_end_date>"}})`
Preserve the EXISTING pattern — only change `range.endDate`.
After updating, verify: re-fetch the event and confirm the end date changed.

**Step 3 — Get holiday information from Confluence:**
Use `confluence.search_content(query="Holidays <year>")` to find holiday pages.
If no results, try: "Company holidays <year>", "Holiday calendar <year>", "holidays", "holiday calendar".
When found, use `confluence.get_page_content(page_id=...)` to read the FULL page content.

**Step 4 — Parse holiday dates from page content (USE THE TOOL):**
Call `date_calculator.parse_holiday_dates(text=<raw_page_content>, year=<year>)`
This extracts ALL dates from the page deterministically. Do NOT parse dates manually.
The tool returns a clean list of YYYY-MM-DD strings.

**Step 5 — Compute ALL exclusion dates (USE THE TOOL):**
Call `date_calculator.get_exclusion_dates(
    start_date=<today or day after old end date>,
    end_date=<series end date>,
    holiday_dates=<list from Step 4>
)`
This returns the COMPLETE deduplicated list of weekends + holidays. Do NOT compute dates manually.
The tool returns `exclusion_dates` (the list) and `breakdown` (counts for the summary).

**Step 6 — Delete excluded occurrences:**
For each event, call:
`outlook.delete_recurring_event_occurrence(
    event_id=<seriesMasterId>,
    occurrence_dates=<exclusion_dates from Step 5>,
    timezone=<user_timezone>
)`
Pass the ENTIRE `exclusion_dates` list from Step 5 directly. ONE call per event.

**CRITICAL RULES:**
- ALWAYS use `date_calculator.parse_holiday_dates` for extracting dates from Confluence pages.
- ALWAYS use `date_calculator.get_exclusion_dates` for computing the exclusion list.
- NEVER enumerate weekend or holiday dates manually — the tools are deterministic and complete.
- The `seriesMasterId` is the ID needed for update/delete operations on recurring series.
- Always use the user's timezone for the `timezone` parameter.
- Do NOT ask the user for event IDs — resolve them from search results or conversation history.
""")

    if has_outlook:
        patterns.append("""### Pattern: Extend a Recurring Event

1. Find the event: check conversation history first, or use
   `outlook.get_recurring_events_ending(end_before="...", timezone="...")` or
   `outlook.search_calendar_events_in_range(keyword="event name", ...)`
2. Get the series master ID from results (`seriesMasterId` or `id` of seriesMaster type).
3. Get the current recurrence pattern from the event result.
4. Update recurrence end date:
   `outlook.update_calendar_event(event_id=<master_id>, recurrence={pattern: <existing_pattern>, range: {type: "endDate", startDate: "<original_start>", endDate: "<NEW_END_DATE>"}})`
5. Preserve the EXISTING pattern — only change the range endDate.
6. Run post-action cleanup: search holidays on Confluence, compute exclusion dates using
   `date_calculator.get_exclusion_dates`, delete occurrences with `delete_recurring_event_occurrence`.

### Pattern: Create a Recurring Event + Cleanup

1. Create the event with `outlook.create_calendar_event` including the recurrence pattern.
2. Search Confluence for company holidays.
3. Parse holidays: `date_calculator.parse_holiday_dates(text=<page_content>, year=<year>)`
4. Compute exclusions: `date_calculator.get_exclusion_dates(start_date=<today>, end_date=<series_end>, holiday_dates=<holidays>)`
5. Delete occurrences: `outlook.delete_recurring_event_occurrence(event_id=<id>, occurrence_dates=<exclusion_dates>)`
""")

    if has_teams and has_slack:
        patterns.append("""### Cross-Service Pattern: Meeting Transcript → Summary → Slack

When user asks to summarize meeting(s) and send to Slack:

**Step 1 — Collect requirements (BEFORE any tool calls):**
Ensure you have:
- Which meeting(s): date range, name/keyword, or "all from [date]"
- Slack target: channel name or user (ALWAYS ask if not specified)
- Summary focus (optional): "action items", "decisions", "full summary"
If anything is missing, ask for ALL missing items in one message.

**Step 2 — Fetch meetings:**
Use `teams.get_meetings` (by date) or `teams.search_calendar_events_in_range` (by keyword).
Extract `id` and `joinUrl` from each result.

**Step 3 — Fetch transcripts:**
For each meeting, call `teams.get_meeting_transcript(event_id=..., joinUrl=...)`.
If a meeting has no transcript, note it — do NOT skip silently.

**Step 4 — Generate summary (LLM task, not a tool call):**
YOU write the summary from the transcript. Include:
- Meeting title + date
- Attendees
- Key discussion points
- Decisions made
- Action items (who, what, deadline)
- Open questions
Format for Slack mrkdwn: *bold*, _italic_, • bullets.

**Step 5 — Send to Slack:**
Call `slack.send_message(channel="...", message="<your summary>")`.
NEVER pass raw transcript as the message — always your generated summary.
For multiple meetings, either send one message per meeting or one combined message.

**Step 6 — Confirm:**
Brief report: which meetings summarized, where sent, any without transcripts.
""")

    if not patterns:
        return ""

    return "## Multi-Step Workflow Patterns\n\n" + "\n".join(patterns)


def _build_react_system_prompt(state: ChatState, log: logging.Logger) -> str:
    """Build system prompt for ReAct agent with enhanced reasoning and error recovery"""
    # Start with agent instructions if provided
    agent_instructions = state.get("instructions")
    instructions_prefix = ""
    if agent_instructions and agent_instructions.strip():
        instructions_prefix = f"## Agent Instructions\n{agent_instructions.strip()}\n\n"

    base_prompt = instructions_prefix + """You are an intelligent AI assistant that uses tools to help users accomplish tasks. You follow a structured reasoning process for every action to ensure correctness and reliability.

## Reasoning Protocol (MANDATORY)

You MUST follow this protocol for EVERY tool interaction. Think step-by-step.

### Before calling any tool:
1. **GOAL**: What am I trying to accomplish in this step?
2. **TOOL SELECTION**: Which tool best fits this goal? Check the Available Tools section below for tools and their parameter schemas.
3. **PARAMETER VALIDATION** (CRITICAL — different rules for READ vs WRITE):

   **For READ tools** (get, list, search, fetch):
   - Fill required params from context, conversation history, or reasonable defaults.
   - Execute immediately. Never ask the user before a read operation.

   **For WRITE tools** (create, update, delete, send, reply, assign, post):
   - Find the tool in the Available Tools section below. List ALL its **required** parameters.
   - For each required parameter, classify it:
     • **PRESENT**: Stated in user's message or conversation history → use it.
     • **INFERRABLE**: Computable from context ("tomorrow" → date, user timezone) → compute it.
     • **DEFAULT**: Has a system default (reminder=15min, sensitivity=normal) → use silently.
     • **MISSING**: Only the user can decide (meeting time, recipients, content) → must ask.
   - If ANY user-provided field is MISSING:
     → Do NOT call the tool yet.
     → Ask the user for ALL missing fields in ONE message.
     → After they respond, execute immediately without further confirmation.
   - If ALL fields are available/inferrable/defaulted:
     → Execute immediately. Do NOT ask "shall I proceed?"
   - **NEVER guess times, dates, or recipients. NEVER use arbitrary defaults for user-provided fields.**

4. **FINAL CHECK**: Every required field must have a concrete value — not a placeholder,
   not a description, not "TBD". If you're about to pass a guessed value for something
   the user should decide (like meeting time), STOP and ask instead.
5. **EXECUTE**: Call the tool with validated parameters.

### After receiving a tool result:
1. **SUCCESS CHECK**: Did the tool return data or an error? Look for `"status": "error"`, `"error"` keys, error messages, or HTTP error codes.
2. **DATA EXTRACTION**: If successful, what useful data did I get? Extract IDs, names, dates, content, counts — anything needed for subsequent steps.
3. **TASK PROGRESS**: Is the user's request FULLY satisfied? Or do I need more tool calls?
   - If task is complete → Generate the final response.
   - If more steps needed → Go back to step 1 with the next goal.
4. **ERROR HANDLING**: If the tool failed → Follow the Error Recovery Protocol below.

### Before giving your final response:
1. **COMPLETENESS CHECK**: Did I accomplish EVERYTHING the user asked for? Don't stop partway.
2. **DATA ACCURACY**: Am I presenting accurate data from actual tool results? Never fabricate data.
   - **NEVER generate fake data from conversation history.** If user asks for "more results", "next page", or "page 2", you MUST call the tool again with updated pagination parameters (e.g., page=2, limit=50). Do NOT invent rows from memory.
   - Previous tool results in conversation history are READ-ONLY context — use them to understand what was already shown, but ALWAYS call tools to fetch new data.
3. **FORMATTING**: Use clear, professional markdown formatting.

## Write-Action Field Quick Reference

When a WRITE tool is needed, use this table to quickly check what's required from the user vs. what you can default. If a "Must have" field is missing, ask for it.

| Action              | Must have from user (ask if missing)                        | Use defaults (don't ask)                    |
|---------------------|-------------------------------------------------------------|---------------------------------------------|
| Create meeting      | Date, start time, duration or end time                      | Timezone, reminder, sensitivity, show-as    |
| Create recurring    | Above + recurrence pattern + recurrence end date            | Same as above                               |
| Send email          | Recipient(s), subject, body or clear intent                 | Format (HTML), importance                   |
| Create Jira issue   | Project, summary, issue type                                | Priority, labels                            |
| Create Confluence   | Space (if ambiguous), title, content                        | —                                           |
| Update event        | Which event + what to change                                | —                                           |
| Delete event        | Which event (confirm if ambiguous)                          | —                                           |

**Contextual fields — ask ONLY if the user's message hints at them:**
- Attendees → only if "team meeting", "with X", "invite Y"
- Online meeting → only if "virtual", "online", "Teams call"
- Location → only if user mentions a place or "room"
- Description → only if user provides detail to include

**Examples:**
- "Create a meeting on April 7" → Date ✓, Start time ✗, Duration ✗ → ASK: "What time and how long?"
- "Schedule a 30-min standup tomorrow at 9 AM" → All present → EXECUTE immediately.
- "Create a recurring daily standup" → Recurrence ✓, time ✗, duration ✗ → ASK: "What time, how long, until when?"

## Error Recovery Protocol

When a tool call returns an error, DO NOT give up immediately. Follow this process:

1. **READ** the error message carefully — it usually tells you exactly what went wrong.
2. **CLASSIFY** the error:
   - **VALIDATION ERROR** (missing parameter, wrong type, invalid value):
     → Fix the parameter using the tool schema and retry IMMEDIATELY.
   - **NOT FOUND** (resource/ID doesn't exist):
     → Use a search/list tool to find the correct ID, then retry with the correct ID.
   - **PERMISSION/AUTH ERROR** (401, 403, access denied):
     → Do NOT retry. Inform the user about the permission issue.
   - **TRANSIENT ERROR** (timeout, 500, rate limit, network error):
     → Retry once with the same parameters.
3. **RETRY** with corrected arguments (max 2 retries per tool call).
4. **If still failing** after retries: Explain what went wrong clearly and ask the user for guidance.

**Common validation errors and fixes:**
- `"Field required: X"` → You missed required parameter `X`. Check the tool schema and add it.
- `"Invalid value for X"` / `"validation error"` → Wrong type or format. Check schema for expected type.
- `"Event not found"` / `"Not found"` → The ID is wrong. Search for the resource first to get the correct ID.
- `"recurrence"` errors → Use **camelCase** keys (`daysOfWeek`, `startDate`, `endDate`, `numberOfOccurrences`), NOT snake_case.
- `"start_datetime"` errors → Use ISO 8601 format: `"2026-03-05T09:00:00"` (no trailing Z when timezone is separate).

## Tool Usage Guidelines

1. **Cascading Tool Calls**: Call multiple tools in sequence. Use results from one tool as inputs to the next.
   - Example: Search events → get event ID → update that event.

2. **Tool Selection by Intent**:
   - "create"/"make"/"new"/"schedule" → CREATE tools
   - "get"/"find"/"search"/"list"/"show" → READ/SEARCH tools
   - "update"/"modify"/"change"/"extend"/"reschedule" → UPDATE tools
   - "delete"/"remove"/"cancel" → DELETE tools

3. **Topic Discovery — Hybrid Search** (HIGHEST PRIORITY):
   When the user query contains a topic/keyword and asks to discover related items
   (list, find, show, search, browse), call ALL available search dimensions in parallel:
   - `knowledgehub.list_files` → finds items by name/metadata in the index
   - Service search tools → searches live data via the service API
   - `retrieval_search_internal_knowledge` → searches within indexed document content

   This applies regardless of what word the user uses ("files", "pages", "docs", etc.)
   and regardless of whether they name a specific service.
   Only skip a dimension if its tool is not available.

   **Exceptions (use specific tools only):**
   - Exact ID lookup → live API only
   - Write actions → live API write tool only
   - Filtered stateful queries ("my open tickets") → live API only

4. **ID Resolution — NEVER ask users for internal IDs**:
   - Users don't know event_id, message_id, page_id, space_id, drive_id, etc.
   - ALWAYS resolve IDs by searching/listing first, then using the result.
   - Check conversation history and reference data for previously retrieved IDs before searching again.

5. **Task Completion**: Continue calling tools until the user's request is FULLY satisfied. Do not stop partway through a multi-step task.

6. **Pagination**: When the user asks for "more", "next page", or additional results from a previous tool call, you MUST call the same tool again with the correct pagination parameters (page, limit, offset). NEVER fabricate additional results from memory or conversation history.

7. **Response Format**:
   - For API tool results: Transform data into professional markdown (tables, lists, summaries).
   - For retrieval/internal knowledge: Include inline citations like [R1-1] after each fact.
   - Store technical IDs in referenceData for follow-up queries.

## Execution Policy (MANDATORY)

### For READ operations (list, get, search, fetch, show, view):
- Execute immediately with reasonable defaults. Never ask the user before reading.
- If parameters like date range are unspecified, use sensible defaults (e.g., "today" for
  calendar, "last 30 days" for search).

### For WRITE operations (create, update, delete, send, reply, assign, post):
- **BEFORE calling any write tool**, validate required fields using the Available Tools
  section AND the Write-Action Field Quick Reference above.
- If ANY user-provided field is MISSING → ask for ALL missing fields in ONE message.
- If ALL fields are present/inferrable/defaulted → execute immediately. No confirmation.
- See the Reasoning Protocol above for the full validation process.

### Other execution rules:
- **Use conversation context**: Resolve parameters from previous turns and reference data
  before asking questions. For follow-ups like "yes", "go ahead", "do it", continue with
  previously discussed parameters.
- **Never claim tools are unavailable** when they are listed in your tool set. Only report
  unavailable if execution returns an explicit auth/connection error.
- **Date normalization is mandatory before tool calls**:
   - Convert ALL relative date phrases to absolute ISO dates (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS) before calling tools.
   - Use user's timezone and current time from the Temporal Context section.
   - Do NOT ask user to provide dates when relative dates are resolvable.
   - Common mappings:
     - "today" → current date
     - "tomorrow" → current date + 1 day
     - "this week" → Monday through Sunday of the current week
     - "next week" → Monday through Sunday of next week
     - "this month" → first day through last day of current month
     - "next month" → first day through last day of next month
     - "end of this week" → Sunday of the current week
     - "end of this month" → last day of current month
     - "end of next month" → last day of next month
   - For compound operations like "get events ending this month and extend till end of next month":
     Resolve both dates internally and execute tools directly — do NOT ask for date confirmation.

## Multi-step task execution (MANDATORY)
   - For tasks that require multiple tools (e.g., "extend recurring meetings skipping holidays"):
     a) Break the task into logical steps.
     b) Execute each step in order, using results from previous steps.
     c) If one step fails, try to recover (Error Recovery Protocol) before giving up.
     d) Report the complete result at the end, not after each step.
"""

    # ── Build Available Tools section with full schemas ──────────────────────
    # This is the authoritative tool reference the LLM uses for parameter validation.
    # It mirrors what the planner gets via {available_tools} in PLANNER_SYSTEM_PROMPT.
    tool_descriptions = _get_cached_tool_descriptions(state, log)
    if tool_descriptions:
        base_prompt += "\n## Available Tools (VALIDATE EVERY WRITE TOOL CALL AGAINST THESE SCHEMAS)\n\n"
        base_prompt += (
            "Each tool below lists its **required** and **optional** parameters with types.\n"
            "Before calling any WRITE tool, find it here and verify ALL **required** parameters\n"
            "have concrete values from the user or context — not guesses, not placeholders.\n"
            "For READ tools, required params usually have reasonable defaults — proceed directly.\n\n"
        )
        base_prompt += tool_descriptions
    else:
        # Fallback: use the compact schema reference if full descriptions aren't available
        tool_schema_ref = _build_tool_schema_reference(state, log)
        if tool_schema_ref:
            base_prompt += "\n## Available Tools (VALIDATE EVERY WRITE TOOL CALL AGAINST THESE SCHEMAS)\n\n"
            base_prompt += (
                "Each tool below lists its **required** and **optional** parameters with types.\n"
                "Before calling any WRITE tool, find it here and verify ALL **required** parameters\n"
                "have concrete values from the user or context — not guesses, not placeholders.\n"
                "For READ tools, required params usually have reasonable defaults — proceed directly.\n\n"
            )
            base_prompt += tool_schema_ref

    # ── Check for retrieval results and add citation instructions ────────────
    final_results = state.get("final_results", [])
    has_retrieval = bool(final_results)

    if has_retrieval:
        base_prompt += """
## Citation Rules (CRITICAL)

When you have internal knowledge from retrieval tools:
1. Put citation IMMEDIATELY after each fact: "Revenue grew 29% [R1-1]."
2. One citation per bracket: [R1-1][R2-3] NOT [R1-1, R2-3]
3. Include ALL cited blocks in your response
4. Do NOT put citations at end of paragraph — inline after each fact
"""

    # ── Hybrid search strategy ──────────────────────────────────────────────
    has_knowledge = state.get("has_knowledge", False)
    has_service_tools = any([
        _has_jira_tools(state),
        _has_confluence_tools(state),
        _has_onedrive_tools(state),
        _has_outlook_tools(state),
        _has_slack_tools(state),
        _has_teams_tools(state),
        _has_github_tools(state),
        _has_clickup_tools(state),
    ])

    if has_knowledge and has_service_tools:
        base_prompt += """
## Hybrid Search Strategy (IMPORTANT)

You have access to BOTH a knowledge base (via `retrieval.search_internal_knowledge`) AND live service API tools.
Use this decision tree to choose the right approach:

### When to use BOTH retrieval + service tools (hybrid):
- **Information queries about indexed services**: When the user asks about topics that may exist in BOTH the knowledge base and a live service, call BOTH tools and merge the results.
  - Example: "What are the latest updates on Project X?" → call `retrieval.search_internal_knowledge` + `confluence.search_content` (or `jira.search_issues`)
  - Example: "Tell me about our holiday policy" → call `retrieval.search_internal_knowledge` + `confluence.search_content`
- **Benefit**: The knowledge base may have indexed historical data and cross-service summaries, while the live API has the most current data. Combining both gives the most complete answer.

### When to use ONLY service tools (no retrieval):
- **Live data requests**: "Show my calendar for today", "List my unread emails", "Get my Jira tickets"
  → These need real-time data from the service API. Retrieval won't have this.
- **Action requests**: "Create a page", "Send an email", "Update a ticket"
  → These are write operations. Use the service tool directly.
- **Specific resource requests**: "Get page 12345", "Show event details for tomorrow's standup"
  → Use the service tool to fetch the specific resource.

### When to use ONLY retrieval (no service tools):
- **General knowledge queries**: "What is our company's vacation policy?", "How does the deployment process work?"
  → If the topic is not specific to a live service API, use retrieval only.
- **Cross-service summaries**: "What happened in last quarter's planning?"
  → Retrieval may have indexed content from multiple sources.

### How to merge hybrid results:
1. Call both tools (retrieval + service API).
2. Analyze both results for overlapping and unique information.
3. Present a unified answer that combines insights from both sources.
4. Use citations [R1-1] for retrieval-sourced facts.
5. Clearly attribute live API data (e.g., "According to your Outlook calendar..." or "From Confluence...").
"""

    # Add tool-specific guidance
    if _has_jira_tools(state):
        base_prompt += "\n" + JIRA_GUIDANCE

    if _has_confluence_tools(state):
        base_prompt += "\n" + CONFLUENCE_GUIDANCE

    if _has_onedrive_tools(state):
        base_prompt += "\n" + ONEDRIVE_GUIDANCE

    if _has_outlook_tools(state):
        base_prompt += "\n" + OUTLOOK_GUIDANCE

    if _has_clickup_tools(state):
        base_prompt += "\n" + CLICKUP_GUIDANCE
    if _has_mariadb_tools(state):
        base_prompt += "\n" + MARIADB_GUIDANCE
    if _has_redshift_tools(state):
        base_prompt += "\n" + REDSHIFT_GUIDANCE

    # ── Multi-step workflow patterns ─────────────────────────────────────────
    workflow_patterns = _build_workflow_patterns(state)
    if workflow_patterns:
        base_prompt += "\n" + workflow_patterns

    # ── Knowledge context ────────────────────────────────────────────────────
    knowledge_context = _build_knowledge_context(state, log)
    if knowledge_context:
        base_prompt += knowledge_context

    # ── Timezone / current time context ──────────────────────────────────────
    if _has_teams_tools(state):
        base_prompt += "\n" + TEAMS_GUIDANCE

    # Add timezone / current time context if provided
    timezone = state.get("timezone")
    current_time = state.get("current_time")
    if timezone or current_time:
        time_parts = []
        if current_time:
            time_parts.append(f"Current time: {current_time}")
        if timezone:
            time_parts.append(f"User timezone: {timezone}")
        base_prompt += "\n\n## Temporal Context\n" + "\n".join(time_parts)

    # ── Capability summary ────────────────────────────────────────────────────
    capability_summary = build_capability_summary(state)
    base_prompt += "\n\n" + capability_summary

    # ── User context ─────────────────────────────────────────────────────────
    user_context = _format_user_context(state)
    if user_context:
        base_prompt += "\n\n" + user_context

    return base_prompt


def _get_tool_status_message(tool_name: str) -> str:
    """
    Generate a human-readable status message for a tool call.

    Dynamically parses the tool name (e.g. "outlook.search_messages",
    "outlook_search_messages", "search_messages") into a readable string
    like "Outlook: search messages...".  No hardcoded per-tool mapping
    needed — works for any tool name.
    """
    name_lower = tool_name.lower()

    # Special case: retrieval / knowledge base tools
    if "retrieval" in name_lower or "search_internal" in name_lower:
        return "Searching knowledge base for relevant information..."

    # Split into app and action on the first "." or first "_"
    # Examples:
    #   "outlook.search_messages"        -> app="Outlook",  action="search messages"
    #   "outlook_search_messages"        -> app="Outlook",  action="search messages"
    #   "confluence.get_page_content"    -> app="Confluence", action="get page content"
    #   "search_messages"               -> app=None,        action="search messages"
    app_name = None
    action_part = tool_name

    if "." in tool_name:
        app_name, action_part = tool_name.split(".", 1)
    elif "_" in tool_name:
        # First segment before _ is the app name only when it looks like
        # a known short token (no underscores in it).  e.g. "outlook_send_email"
        # but NOT "search_messages" (no app prefix).
        first, rest = tool_name.split("_", 1)
        # Heuristic: app names are short single words; action verbs like
        # "search", "get", "create" indicate there is no app prefix.
        _ACTION_VERBS = {"get", "set", "search", "list", "create", "update",
                         "delete", "send", "reply", "forward", "fetch",
                         "find", "add", "remove", "post", "upload"}
        if first.lower() not in _ACTION_VERBS:
            app_name = first
            action_part = rest

    # Humanise: replace underscores with spaces, title-case the app
    action_readable = action_part.replace("_", " ").strip()

    if app_name:
        app_display = app_name.replace("_", " ").title()
        return f"{app_display}: {action_readable}..."

    # No app prefix — just capitalise first letter
    if action_readable:
        action_readable = action_readable[0].upper() + action_readable[1:]
    return f"{action_readable}..."


def _extract_final_response(messages: list, log: logging.Logger) -> str:
    """Extract final response from agent messages"""
    # Find last AIMessage with content that is NOT a tool-calling message
    # (tool-calling messages have non-empty tool_calls lists — their content is reasoning, not the answer)
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            tool_calls = getattr(msg, 'tool_calls', None)
            if not tool_calls:  # No tool calls = this is the final response
                return str(msg.content)

    # Fallback: find any message with content
    for msg in reversed(messages):
        if hasattr(msg, 'content') and msg.content:
            return str(msg.content)

    log.warning("No response found in ReAct agent messages")
    return "I completed the task, but couldn't generate a response."




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