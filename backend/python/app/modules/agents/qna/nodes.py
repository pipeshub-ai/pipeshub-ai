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
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter

from app.modules.agents.qna.chat_state import ChatState
from app.modules.agents.qna.stream_utils import safe_stream_write
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


def _get_tool_status_msg(tool_name: str) -> str:
    """Human-readable 'in-progress' message for a tool."""
    n = tool_name.lower()
    if "retrieval" in n:
        return "Searching knowledge base..."
    if "slack" in n:
        if "send" in n:
            return "Sending Slack message..."
        if "history" in n or "message" in n:
            return "Reading Slack messages..."
        if "conversation" in n:
            return "Fetching Slack conversations..."
        return "Working with Slack..."
    if "confluence" in n:
        if "create" in n:
            return "Creating Confluence page..."
        if "update" in n:
            return "Updating Confluence page..."
        return "Retrieving Confluence content..."
    if "jira" in n:
        if "create" in n:
            return "Creating Jira issue..."
        if "update" in n:
            return "Updating Jira issue..."
        return "Searching Jira..."
    if "gmail" in n or ("email" in n and "outlook" not in n):
        return "Sending email..." if "send" in n else "Fetching emails..."
    if "outlook" in n or "calendar" in n:
        return "Sending email..." if "send" in n else "Fetching calendar/emails..."
    if "drive" in n or "google" in n:
        return "Searching Drive..." if ("search" in n or "list" in n) else "Accessing Drive..."
    display = tool_name.replace("_", " ").replace(".", " ").title()
    return f"Executing {display}..."


def _get_tool_done_msg(tool_name: str) -> str:
    """Human-readable 'completed' message for a tool."""
    n = tool_name.lower()
    if "retrieval" in n:
        return "Knowledge base search completed"
    if "slack" in n:
        return "Slack message sent" if "send" in n else "Slack operation completed"
    if "confluence" in n:
        if "create" in n:
            return "Confluence page created"
        if "update" in n:
            return "Confluence page updated"
        return "Confluence operation completed"
    if "jira" in n:
        if "create" in n:
            return "Jira issue created"
        if "update" in n:
            return "Jira issue updated"
        return "Jira operation completed"
    if "gmail" in n or ("email" in n and "outlook" not in n):
        return "Email sent" if "send" in n else "Emails fetched"
    if "outlook" in n or "calendar" in n:
        return "Email sent" if "send" in n else "Calendar/emails fetched"
    display = tool_name.replace("_", " ").replace(".", " ").title()
    return f"{display} completed"


# ============================================================================
# TOOL RESULT PROCESSING - RELIABLE EXTRACTION
# ============================================================================

class ToolResultExtractor:
    """Reliable extraction of data from tool results"""

    @staticmethod
    def extract_success_status(result: Union[Dict[str, Any], str, Tuple[bool, Any], None]) -> bool:
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
    def extract_data_from_result(result: Union[Dict[str, Any], str, Tuple[bool, Any], List[Any], None]) -> Union[Dict[str, Any], str, List[Any], None]:
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
    def extract_field_from_data(data: Union[Dict[str, Any], List[Any], str, None], field_path: List[str]) -> Optional[Union[Dict[str, Any], List[Any], str, int, float, bool]]:
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
                                for list_key in ("items", "results", "records", "values", "data"):
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
    def strip_unresolved(
        cls,
        args: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], List[str]]:
        """
        Replace any remaining unresolved {{...}} placeholders with None.

        This allows tool calls to proceed when only *optional* fields have
        unresolved placeholders.  Required fields that are None will be caught
        by Pydantic validation in _validate_and_normalize_args.

        Returns:
            (cleaned_args, list_of_placeholder_names_that_were_stripped)
        """
        stripped: List[str] = []

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
    ) -> Optional[Union[Dict[str, Any], List[Any], str, int, float, bool]]:
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
            # Show the actual structure to aid debugging (upgrade to WARNING so it's visible)
            if isinstance(tool_data, dict):
                log.warning(f"  Tool data top-level keys: {list(tool_data.keys())}")
                if "data" in tool_data and isinstance(tool_data["data"], dict):
                    data_keys = list(tool_data["data"].keys())
                    log.warning(f"  'data' sub-keys: {data_keys}")
                    # Check each list in data for debugging
                    for list_key in data_keys:
                        val = tool_data["data"][list_key]
                        if isinstance(val, list):
                            log.warning(f"  'data.{list_key}' is a list with {len(val)} items")
                            if len(val) == 0:
                                log.warning(
                                    f"  ⚠️ 'data.{list_key}' list is EMPTY — placeholder index access "
                                    f"cannot succeed. Check that the preceding tool returned data."
                                )
                            elif len(val) > 0 and isinstance(val[0], dict):
                                log.warning(f"  First item keys in 'data.{list_key}': {list(val[0].keys())[:10]}")
                elif isinstance(tool_data, str):
                    log.warning(f"  Tool data is a raw string (len={len(tool_data)}): {tool_data[:100]}")
            elif isinstance(tool_data, str):
                log.warning(f"  Tool data is a raw string (len={len(tool_data)}): {tool_data[:100]}")

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
        - "jira.search_users.data[0].accountId" → ("jira.search_users", ["data", "0", "accountId"])
        - "jira.search_users.data.results[0].accountId" → ("jira.search_users", ["data", "0", "accountId"]) (removes .results)
        - "create_issue.key" → ("jira.create_issue", ["key"]) if fuzzy match

        Returns:
            (tool_name, field_path) or (None, []) if can't parse
        """
        # Helper function to parse field path with array indices
        def parse_field_path(path_str: str) -> List[str]:
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
            if placeholder.startswith(tool_name + '.'):
                # Extract field path
                remaining = placeholder[len(tool_name) + 1:]
                field_path = parse_field_path(remaining)
                # Don't remove .results here - let extract_field_from_data handle it
                # It will check if results exists and only fallback if it doesn't
                return tool_name, field_path

        # Fuzzy match - find tool that matches end of placeholder
        parts = placeholder.split('.')
        if len(parts) >= MIN_PLACEHOLDER_PARTS:
            # Try matching the first part against tool names
            prefix = parts[0]
            # Reconstruct remaining path and parse it
            remaining = '.'.join(parts[1:])
            field_path = parse_field_path(remaining)
            # Don't remove .results here - let extract_field_from_data handle it

            for tool_name in sorted_tools:
                # Normalize for comparison
                normalized_tool = tool_name.lower().replace('_', '').replace('.', '')
                normalized_prefix = prefix.lower().replace('_', '')

                if normalized_prefix in normalized_tool or normalized_tool.endswith(normalized_prefix):
                    return tool_name, field_path

        return None, []

    # ── LLM-mediated batch resolution ────────────────────────────────────────

    # Metadata fields added by API wrappers — never useful for placeholder resolution
    _RESULT_META_FIELDS: frozenset = frozenset(
        {"success", "error", "message", "ok", "status", "count", "total", "next_cursor"}
    )

    @classmethod
    def _find_main_list(cls, data: Any) -> Optional[List]:
        """
        Recursively find the primary list inside a tool result.

        Follows the common SlackResponse / API-wrapper pattern:
            {success, data: {conversations: [...], count: N}}
        by first descending into 'data', then searching all values for lists.
        """
        if isinstance(data, list):
            return data if data else None

        if not isinstance(data, dict):
            return None

        # Prefer the 'data' envelope first (SlackResponse, most REST wrappers)
        for key in ("data", "results", "items", "records", "values"):
            if key in data:
                found = cls._find_main_list(data[key])
                if found is not None:
                    return found

        # Scan all non-metadata values for any list
        for k, v in data.items():
            if k in cls._RESULT_META_FIELDS:
                continue
            if isinstance(v, list) and v:
                return v

        # Recurse into nested dicts (one level deep)
        for k, v in data.items():
            if k in cls._RESULT_META_FIELDS:
                continue
            if isinstance(v, dict):
                found = cls._find_main_list(v)
                if found is not None:
                    return found

        return None

    @classmethod
    def _build_focused_context(
        cls,
        results_by_tool: Dict[str, Any],
        blocked_tools: List[Dict[str, Any]],
        log: logging.Logger,
    ) -> str:
        """
        Build a minimal, focused context string for the LLM.

        Instead of dumping entire tool results (which can be 15 000+ chars for
        50 Slack channels), this:
        1. Parses all {{placeholders}} to find which source tool + array index +
           leaf field are needed.
        2. Navigates to the actual list inside each result.
        3. Returns ONLY the items (up to max index + 1) with ONLY the needed
           fields — typically < 600 chars for 10 Slack channels.
        """
        INDEX_RE = re.compile(r'\[(\d+)\]')
        PLACEHOLDER_RE = re.compile(r'\{\{([^}]+)\}\}')

        # Collect per-source-tool needs: max index accessed + leaf fields
        source_needs: Dict[str, Dict[str, Any]] = {}

        for tc in blocked_tools:
            args_str = json.dumps(tc.get("args", {}), default=str)
            for m in PLACEHOLDER_RE.finditer(args_str):
                ph = m.group(1)
                indices = [int(x) for x in INDEX_RE.findall(ph)]
                if not indices:
                    continue  # Non-indexed placeholders resolved by static resolver
                max_idx = max(indices)
                # Leaf field = token after the last ']'
                leaf = INDEX_RE.split(ph)[-1].lstrip(".")

                # Match placeholder to a known source tool (longest match wins)
                source_tool: Optional[str] = None
                ph_flat = ph.replace("_", "").replace(".", "").lower()
                for tool_name in sorted(results_by_tool.keys(), key=len, reverse=True):
                    tool_flat = tool_name.replace("_", "").replace(".", "").lower()
                    if ph_flat.startswith(tool_flat) or tool_flat in ph_flat:
                        source_tool = tool_name
                        break
                # Fallback: first available result
                if source_tool is None and results_by_tool:
                    source_tool = next(iter(results_by_tool))

                if source_tool is None:
                    continue

                needs = source_needs.setdefault(source_tool, {"max_index": 0, "fields": set()})
                needs["max_index"] = max(needs["max_index"], max_idx)
                if leaf:
                    needs["fields"].add(leaf)

        if not source_needs:
            return ""  # Caller will fall back to compact full-result dump

        ctx_parts: List[str] = []
        for tool_name, needs in source_needs.items():
            result_data = results_by_tool.get(tool_name)
            if result_data is None:
                continue

            max_idx: int = needs["max_index"]
            fields: set = needs["fields"]

            lst = cls._find_main_list(result_data)
            if lst is not None:
                n = min(max_idx + 1, len(lst))
                rows: List[str] = []
                for i, item in enumerate(lst[:n]):
                    if isinstance(item, dict):
                        entry: Dict[str, Any] = {}
                        if fields:
                            for f in fields:
                                if f in item:
                                    entry[f] = item[f]
                        # Always include common identifier fields so the LLM
                        # can orient itself even if not explicitly requested.
                        for idf in ("id", "name", "key", "channel_id", "ts"):
                            if idf not in entry and idf in item:
                                entry[idf] = item[idf]
                        if not entry:
                            entry = item  # fallback: full item
                    else:
                        entry = {"value": item}  # type: ignore[assignment]
                    rows.append(f"  [{i}]: {json.dumps(entry, default=str)}")
                ctx_parts.append(
                    f"**{tool_name}** — {n} of {len(lst)} items:\n" + "\n".join(rows)
                )
            else:
                # No list found — show compact filtered dict
                if isinstance(result_data, dict):
                    filtered = {
                        k: v for k, v in result_data.items()
                        if k not in cls._RESULT_META_FIELDS
                    }
                    compact = json.dumps(filtered, default=str, ensure_ascii=False)
                else:
                    compact = str(result_data)
                ctx_parts.append(f"**{tool_name}**:\n{compact[:3000]}")

        return "\n\n".join(ctx_parts)

    @classmethod
    def _parse_json_array(cls, content: str) -> Optional[List]:
        """
        Robustly extract a JSON array from raw LLM output.

        Handles:
        - Markdown code fences (```json ... ```)
        - Leading/trailing prose
        - Trailing commas  (common LLM mistake)
        - Nested objects/arrays (bracket-depth scan, not regex)
        """
        # 1. Strip markdown fences
        content = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.MULTILINE)
        content = re.sub(r"\s*```$", "", content.strip(), flags=re.MULTILINE)
        content = content.strip()

        # 2. Try direct parse
        try:
            result = json.loads(content)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

        # 3. Bracket-depth scan to extract the outermost [...]
        start = content.find("[")
        if start == -1:
            return None

        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(content)):
            c = content[i]
            if escape:
                escape = False
                continue
            if c == "\\" and in_string:
                escape = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    candidate = content[start : i + 1]
                    # Try as-is
                    try:
                        result = json.loads(candidate)
                        if isinstance(result, list):
                            return result
                    except json.JSONDecodeError:
                        pass
                    # Remove trailing commas before } or ]
                    cleaned = re.sub(r",\s*([}\]])", r"\1", candidate)
                    try:
                        result = json.loads(cleaned)
                        if isinstance(result, list):
                            return result
                    except json.JSONDecodeError:
                        pass
                    break

        return None

    @classmethod
    async def llm_batch_resolve(
        cls,
        blocked_tools: List[Dict[str, Any]],
        results_by_tool: Dict[str, Any],
        llm: "BaseChatModel",
        log: logging.Logger,
    ) -> List[Dict[str, Any]]:
        """
        Use ONE LLM call to batch-resolve all blocked tool arguments.

        Key properties:
        - **Focused context**: parses placeholders to identify exactly which
          list items / fields are needed, then shows ONLY those rows.  For 50
          Slack channels with 10 dependent tools this is ~600 chars instead of
          a 15 000-char truncated blob.
        - **Robust parsing**: bracket-depth JSON extraction, trailing-comma
          cleanup, markdown-fence stripping.
        - **Validation**: any resolved args that still contain {{...}} are
          rejected so the caller can fall back gracefully.

        Returns the same list with resolved args in place; returns the original
        list unchanged if resolution fails.
        """
        from langchain_core.messages import HumanMessage

        try:
            # 1. Build focused context (smart extraction of only needed data)
            context = cls._build_focused_context(results_by_tool, blocked_tools, log)

            if not context:
                # Fallback: compact full results (capped at 5000 chars per tool)
                ctx_parts: List[str] = []
                for tn, result in results_by_tool.items():
                    try:
                        s = json.dumps(result, default=str, ensure_ascii=False)
                        if len(s) > 5000:
                            s = s[:5000] + "\n... (truncated)"
                    except Exception:
                        s = str(result)[:5000]
                    ctx_parts.append(f"**{tn}**:\n{s}")
                context = "\n\n".join(ctx_parts)

            # 2. Build tool request list
            tool_requests = [
                {
                    "index": i,
                    "tool": tc.get("_actual_name", tc.get("name", "")),
                    "args": tc.get("args", {}),
                }
                for i, tc in enumerate(blocked_tools)
            ]

            prompt = (
                "Resolve cascading tool call arguments.\n"
                "Replace each {{placeholder}} with the actual value from the data below.\n\n"
                "## Completed tool data:\n"
                f"{context}\n\n"
                "## Tools to resolve:\n"
                f"{json.dumps(tool_requests, indent=2, default=str)}\n\n"
                "Rules:\n"
                "- Extract the exact string/ID value (no descriptions)\n"
                "- For [N] in the placeholder path: use the item at index N from the list\n"
                "- Preserve all non-placeholder values exactly as-is\n"
                "- Every {{placeholder}} MUST be replaced\n\n"
                "Return ONLY a JSON array (no markdown, no explanation):\n"
                '[{"index": 0, "resolved_args": {"key": "actual_value", ...}}, ...]'
            )

            # 3. Call LLM
            response = await llm.ainvoke([HumanMessage(content=prompt)])
            raw = response.content.strip()

            # 4. Parse response
            resolved_list = cls._parse_json_array(raw)

            if not isinstance(resolved_list, list):
                log.warning("⚠️ LLM batch resolve: response was not a JSON array")
                return blocked_tools

            # 5. Apply resolved args with placeholder validation
            result_tools = [dict(tc) for tc in blocked_tools]
            resolved_count = 0
            PLACEHOLDER_CHECK = re.compile(r'\{\{[^}]+\}\}')

            for item in resolved_list:
                idx = item.get("index")
                resolved_args = item.get("resolved_args")

                if not (isinstance(idx, int) and 0 <= idx < len(result_tools)):
                    continue
                if not isinstance(resolved_args, dict):
                    continue

                # Reject if LLM left any {{...}} unresolved
                if PLACEHOLDER_CHECK.search(json.dumps(resolved_args, default=str)):
                    tool_id = result_tools[idx].get("_actual_name", result_tools[idx].get("name"))
                    log.warning(f"⚠️ [{idx}] {tool_id}: still has unresolved placeholders — skipped")
                    continue

                result_tools[idx] = {**result_tools[idx], "args": resolved_args}
                resolved_count += 1
                tool_id = result_tools[idx].get("_actual_name", result_tools[idx].get("name"))
                log.debug(f"✅ [{idx}] {tool_id}: {str(resolved_args)[:120]}")

            log.info(f"🎯 LLM batch resolve: {resolved_count}/{len(blocked_tools)} tools resolved")
            return result_tools

        except Exception as e:
            log.error(f"❌ LLM batch resolve error: {e}", exc_info=True)
            return blocked_tools


# ============================================================================
# CASCADING EXECUTOR - LLM-DRIVEN EXPANSION
# ============================================================================

class CascadingExecutor:
    """
    LLM-driven cascading tool execution with result-based expansion.

    Core difference vs the old placeholder-resolution approach:
    - Old: planner generates N calls with {{placeholder[i]}} → resolve each 1:1
    - New: planner generates 1 template call with {{placeholder}} → LLM sees
      actual results → generates the COMPLETE call list  (1 → N expansion)

    Example — "show me all Slack conversations for last 3 days":
      Planner plans:  get_user_conversations + get_channel_history({{[0].id}})
      Phase 1:        get_user_conversations → 30 conversations
      LLM expansion:  "for each of the 30 conversations call get_channel_history"
      Phase 2:        30 parallel get_channel_history calls
    """

    MAX_EXPANDED_CALLS: int = 50   # safety cap on expanded dependent calls

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _tag_tools(
        planned_tools: List[Dict[str, Any]],
        tools_by_name: Dict[str, Any],
        llm: "BaseChatModel",
    ) -> List[Dict[str, Any]]:
        """Attach '_actual_name' to each planned tool dict."""
        from app.modules.agents.qna.tool_system import _sanitize_tool_name_if_needed
        tagged = []
        for tc in planned_tools:
            raw = tc.get("name", "")
            norm = _sanitize_tool_name_if_needed(raw, llm) if llm else raw
            actual = norm if norm in tools_by_name else raw
            tagged.append({**tc, "_actual_name": actual})
        return tagged

    @staticmethod
    def _split_independent_dependent(
        tagged_tools: List[Dict[str, Any]],
        results_by_tool: Dict[str, Any],
        log: logging.Logger,
    ) -> tuple:
        """Separate tools that are ready to execute from those blocked by placeholders."""
        independent: List[Dict[str, Any]] = []
        dependent: List[Dict[str, Any]] = []

        for tc in tagged_tools:
            args = tc.get("args", {})
            if not PlaceholderResolver.has_placeholders({"args": args}):
                independent.append(tc)
            else:
                # Try static resolution first (cheap, no LLM)
                resolved = PlaceholderResolver.resolve_all(args, results_by_tool, log)
                if not PlaceholderResolver.has_placeholders(resolved):
                    independent.append({**tc, "args": resolved})
                else:
                    dependent.append(tc)

        return independent, dependent

    @staticmethod
    async def _run_batch(
        tools: List[Dict[str, Any]],
        tools_by_name: Dict[str, Any],
        state: ChatState,
        log: logging.Logger,
        writer: StreamWriter,
        config: RunnableConfig,
        phase_label: str,
    ) -> List[Dict[str, Any]]:
        """Run a batch of tools in parallel, returning raw result dicts."""
        tasks = []
        valid_tc: List[Dict[str, Any]] = []

        for tc in tools:
            actual = tc.get("_actual_name", tc.get("name", ""))
            if actual not in tools_by_name:
                log.warning(f"  ⚠️ Tool not found: {tc.get('name')} — skipping")
                continue
            safe_stream_write(writer, {"event": "status", "data": {
                "status": "executing",
                "message": _get_tool_status_msg(actual),
            }}, config)
            valid_tc.append(tc)
            tasks.append(ToolExecutor._execute_single_tool(
                tool=tools_by_name[actual],
                tool_name=actual,
                tool_args=tc.get("args", {}),
                tool_id=f"{phase_label}_{actual}",
                state=state,
                log=log,
            ))

        if not tasks:
            return []

        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        results: List[Dict[str, Any]] = []
        for raw, tc in zip(raw_results, valid_tc):
            actual = tc.get("_actual_name", tc.get("name", ""))
            if isinstance(raw, Exception):
                log.error(f"  ❌ {actual}: unhandled exception: {raw}")
                results.append({
                    "tool_name": actual, "status": "error",
                    "result": str(raw), "tool_id": f"{phase_label}_{actual}",
                })
            elif isinstance(raw, dict):
                if raw.get("status") == "success":
                    safe_stream_write(writer, {"event": "status", "data": {
                        "status": "executing",
                        "message": _get_tool_done_msg(actual),
                    }}, config)
                results.append(raw)
        return results

    # ── LLM expansion ─────────────────────────────────────────────────────────

    @classmethod
    async def _llm_expand(
        cls,
        dependent_plan: List[Dict[str, Any]],
        results_by_tool: Dict[str, Any],
        tools_by_name: Dict[str, Any],
        user_query: str,
        llm: "BaseChatModel",
        log: logging.Logger,
    ) -> List[Dict[str, Any]]:
        """
        ONE LLM call that turns a pre-planned template (with {{placeholders}})
        into the COMPLETE list of concrete tool calls based on actual results.

        Unlike llm_batch_resolve (which resolves N pre-planned slots 1:1), this
        generates the full N-call list from scratch — critical when the planner
        only wrote 1 placeholder but the data has 30 items.
        """
        from langchain_core.messages import HumanMessage

        try:
            # Build compact context: ALL items, key fields only
            ctx_parts: List[str] = []
            for tool_name, result_data in results_by_tool.items():
                lst = PlaceholderResolver._find_main_list(result_data)
                if lst is not None:
                    rows = []
                    for i, item in enumerate(lst):
                        if isinstance(item, dict):
                            entry: Dict[str, Any] = {}
                            for k in (
                                "id", "name", "key", "channel_id", "ts", "user",
                                "is_im", "is_private", "is_archived", "is_channel",
                                "is_mpim", "context_team_id", "is_user_deleted",
                            ):
                                if k in item:
                                    entry[k] = item[k]
                            rows.append(f"  [{i}]: {json.dumps(entry, default=str)}")
                        else:
                            rows.append(f"  [{i}]: {json.dumps(item, default=str)}")
                    ctx_parts.append(
                        f"**{tool_name}** — {len(lst)} items:\n" + "\n".join(rows)
                    )
                else:
                    if isinstance(result_data, dict):
                        filtered = {
                            k: v for k, v in result_data.items()
                            if k not in PlaceholderResolver._RESULT_META_FIELDS
                        }
                        ctx_parts.append(
                            f"**{tool_name}**:\n"
                            + json.dumps(filtered, default=str, ensure_ascii=False)[:3000]
                        )
                    else:
                        ctx_parts.append(f"**{tool_name}**: {str(result_data)[:2000]}")
            context = "\n\n".join(ctx_parts)

            # Build template description: what the planner originally planned
            templates: List[Dict[str, Any]] = []
            for tc in dependent_plan:
                actual_name = tc.get("_actual_name", tc.get("name", ""))
                tpl: Dict[str, Any] = {
                    "name": actual_name,
                    "args_template": tc.get("args", {}),
                }
                if actual_name in tools_by_name:
                    t = tools_by_name[actual_name]
                    if hasattr(t, "args_schema") and t.args_schema:
                        try:
                            schema = t.args_schema.schema()
                            tpl["arg_types"] = {
                                k: v.get("type", "string")
                                for k, v in schema.get("properties", {}).items()
                            }
                        except Exception:
                            pass
                templates.append(tpl)

            prompt = (
                f'User asked: "{user_query}"\n\n'
                "## Data returned by completed tools:\n"
                f"{context}\n\n"
                "## Planned tool template(s) ({{placeholders}} = values to fill):\n"
                f"{json.dumps(templates, indent=2, default=str)}\n\n"
                "Generate the COMPLETE list of concrete tool calls needed.\n"
                "Rules:\n"
                "- For each relevant item in the data, create one tool call\n"
                "- Use ACTUAL values from the data (no {{placeholders}} in output)\n"
                "- Keep non-placeholder args exactly as in the template\n"
                "- Filter by user intent (e.g. skip archived channels, apply date ranges)\n"
                f"- Maximum {cls.MAX_EXPANDED_CALLS} calls total\n\n"
                "Return ONLY a JSON array (no markdown, no explanation):\n"
                '[{"name": "tool_name", "args": {"key": "actual_value", ...}}, ...]'
            )

            response = await llm.ainvoke([HumanMessage(content=prompt)])
            raw = response.content.strip()

            expanded = PlaceholderResolver._parse_json_array(raw)
            if not isinstance(expanded, list):
                log.warning("  ⚠️ Expand LLM: response was not a JSON array")
                return []

            PLACEHOLDER_CHECK = re.compile(r'\{\{[^}]+\}\}')
            valid: List[Dict[str, Any]] = []

            for item in expanded[:cls.MAX_EXPANDED_CALLS]:
                if not isinstance(item, dict):
                    continue
                name = item.get("name", "")
                args = item.get("args", {})
                if not name or not isinstance(args, dict):
                    continue
                # Reject items with remaining placeholders
                if PLACEHOLDER_CHECK.search(json.dumps(args, default=str)):
                    log.warning(f"  ⚠️ Expand: '{name}' still has placeholders — skipped")
                    continue
                # Resolve actual tool name
                actual = name if name in tools_by_name else None
                if actual is None:
                    name_flat = name.lower().replace("_", "")
                    for tn in tools_by_name:
                        if name_flat in tn.lower().replace("_", "") or tn.lower().replace("_", "") in name_flat:
                            actual = tn
                            break
                if actual is None:
                    log.warning(f"  ⚠️ Expand: tool '{name}' not found — skipped")
                    continue
                valid.append({"name": name, "_actual_name": actual, "args": args})

            log.info(
                f"  🔀 Expand: {len(valid)} concrete calls from {len(expanded)} LLM suggestions"
            )
            return valid

        except Exception as e:
            log.error(f"  ❌ Expand LLM error: {e}", exc_info=True)
            return []

    # ── main entry point ──────────────────────────────────────────────────────

    @classmethod
    async def execute(
        cls,
        planned_tools: List[Dict[str, Any]],
        tools_by_name: Dict[str, Any],
        llm: "BaseChatModel",
        state: ChatState,
        log: logging.Logger,
        writer: StreamWriter,
        config: RunnableConfig,
    ) -> List[Dict[str, Any]]:
        """
        Full cascading execution with LLM-guided expansion.

        Flow:
          Phase 1  — Execute independent tools (no placeholders) in parallel.
          Expand   — ONE LLM call generates the COMPLETE list of dependent calls.
          Phase 2  — Execute all expanded calls in parallel (batched).

        Returns all tool result dicts (combined from both phases).
        """
        all_results: List[Dict[str, Any]] = []
        results_by_tool: Dict[str, Any] = {}

        tagged = cls._tag_tools(planned_tools, tools_by_name, llm)
        independent, dependent = cls._split_independent_dependent(tagged, results_by_tool, log)

        log.info(
            f"🔗 Cascading: {len(independent)} independent | {len(dependent)} dependent"
        )

        # ── Phase 1: independent tools ────────────────────────────────────────
        if independent:
            log.info(f"⚡ Phase 1: {len(independent)} independent tools in parallel")
            phase1 = await cls._run_batch(
                independent, tools_by_name, state, log, writer, config, phase_label="p1"
            )
            for result in phase1:
                all_results.append(result)
                if result.get("status") == "success":
                    actual = result.get("tool_name", "")
                    data = ToolResultExtractor.extract_data_from_result(result.get("result"))
                    if actual:
                        results_by_tool[actual] = data

        if not dependent:
            return all_results

        if not results_by_tool:
            log.warning("  ⚠️ No phase-1 results to expand from — skipping dependent tools")
            return all_results

        # ── Expand: LLM generates the complete dependent call list ────────────
        log.info(f"🤖 Expanding {len(dependent)} template(s) into concrete calls…")
        expanded = await cls._llm_expand(
            dependent_plan=dependent,
            results_by_tool=results_by_tool,
            tools_by_name=tools_by_name,
            user_query=state.get("query", ""),
            llm=llm,
            log=log,
        )

        # Fallback: if expansion failed, try the old batch-resolve (1:1 resolution)
        if not expanded:
            log.warning("  ⚠️ Expansion returned nothing — falling back to batch-resolve")
            expanded = await PlaceholderResolver.llm_batch_resolve(
                dependent, results_by_tool, llm, log
            )

        if not expanded:
            log.warning("  ⚠️ All resolution attempts failed — skipping dependent tools")
            return all_results

        # ── Phase 2: expanded dependent tools in parallel (batched) ──────────
        batch_size = NodeConfig.MAX_PARALLEL_TOOLS
        total = min(len(expanded), cls.MAX_EXPANDED_CALLS)
        log.info(f"⚡ Phase 2: {total} expanded calls (batches of {batch_size})")

        for batch_start in range(0, total, batch_size):
            batch = expanded[batch_start: batch_start + batch_size]
            batch_label = f"p2b{batch_start // batch_size + 1}"
            phase2 = await cls._run_batch(
                batch, tools_by_name, state, log, writer, config, phase_label=batch_label
            )
            all_results.extend(phase2)

        return all_results


# ============================================================================
# TOOL EXECUTION - SEQUENTIAL WITH CASCADING SUPPORT
# ============================================================================

class ToolExecutor:
    """Handles tool execution with cascading support"""

    @staticmethod
    async def execute_tools(
        planned_tools: List[Dict[str, Any]],
        tools_by_name: Dict[str, Any],
        llm: BaseChatModel,
        state: ChatState,
        log: logging.Logger,
        writer: StreamWriter,
        config: RunnableConfig
    ) -> List[Dict[str, Any]]:
        """
        Execute tools - cascading (LLM expansion) or parallel.

        Returns:
            List of tool results with status, result, tool_name, etc.
        """
        # Detect if we need cascading execution
        has_cascading = PlaceholderResolver.has_placeholders(
            {"tools": planned_tools}
        )

        if has_cascading:
            log.info("🔗 Cascading detected - using LLM-expansion executor")
            return await CascadingExecutor.execute(
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
        llm: BaseChatModel,
        state: ChatState,
        log: logging.Logger,
        writer: StreamWriter,
        config: RunnableConfig
    ) -> List[Dict[str, Any]]:
        """
        Execute tools with LLM-mediated cascading dependency resolution.

        Instead of fragile placeholder string extraction, this groups tools into
        dependency phases.  After each phase of independent tools runs in parallel,
        ONE LLM call batch-resolves ALL remaining dependent tool args using the
        actual results — then those tools also run in parallel.

        Flow (per phase):
        1. Separate remaining tools into ready (no unresolved deps) and blocked.
        2. If nothing is ready, issue ONE LLM call to batch-resolve the blocked batch.
        3. Execute ready tools in parallel (up to MAX_PARALLEL_TOOLS at once).
        4. Update results_by_tool; set remaining = blocked and repeat.
        """
        from app.modules.agents.qna.tool_system import _sanitize_tool_name_if_needed

        all_tool_results: List[Dict[str, Any]] = []
        results_by_tool: Dict[str, Any] = {}
        tool_invocation_counts: Dict[str, int] = {}

        # Tag each tool with its normalised actual name upfront
        remaining: List[Dict[str, Any]] = []
        for tc in planned_tools:
            raw_name = tc.get("name", "")
            norm_name = _sanitize_tool_name_if_needed(raw_name, llm) if llm else raw_name
            actual = norm_name if norm_name in tools_by_name else raw_name
            remaining.append({**tc, "_actual_name": actual})

        MAX_PHASES = 4
        phase = 0

        while remaining and phase < MAX_PHASES:
            phase += 1

            # ── Separate into ready (resolved) vs blocked (unresolved deps) ──
            ready: List[Dict[str, Any]] = []
            blocked: List[Dict[str, Any]] = []

            for tc in remaining:
                args = tc.get("args", {})
                if not PlaceholderResolver.has_placeholders({"args": args}):
                    ready.append(tc)
                else:
                    # Try standard static resolution with current results
                    resolved = PlaceholderResolver.resolve_all(args, results_by_tool, log)
                    if not PlaceholderResolver.has_placeholders(resolved):
                        ready.append({**tc, "args": resolved})
                    else:
                        blocked.append(tc)

            # ── If nothing is ready, use LLM to batch-resolve blocked tools ──
            if not ready and blocked:
                if llm and results_by_tool:
                    log.info(
                        f"🤖 LLM cascading: batch-resolving {len(blocked)} "
                        f"dependent tools in 1 LLM call"
                    )
                    try:
                        resolved_batch = await PlaceholderResolver.llm_batch_resolve(
                            blocked, results_by_tool, llm, log
                        )
                        remaining = resolved_batch
                        continue  # Re-evaluate now that args are resolved
                    except Exception as e:
                        log.warning(f"⚠️ LLM batch resolve failed: {e} — stripping placeholders")

                # Fallback: strip remaining placeholders and proceed (old behaviour)
                for tc in blocked:
                    stripped, _ = PlaceholderResolver.strip_unresolved(
                        PlaceholderResolver.resolve_all(tc.get("args", {}), results_by_tool, log)
                    )
                    ready.append({**tc, "args": stripped})
                blocked = []

            if not ready:
                break

            # ── Execute ready batch in parallel ───────────────────────────────
            log.info(f"⚡ Phase {phase}: executing {len(ready)} tools in parallel")

            phase_tasks: List[Any] = []
            valid_tc: List[Dict[str, Any]] = []

            for tc in ready[:NodeConfig.MAX_PARALLEL_TOOLS]:
                actual_name = tc.get("_actual_name", tc.get("name", ""))
                if actual_name not in tools_by_name:
                    log.warning(f"❌ Tool not found: {tc.get('name')}")
                    all_tool_results.append({
                        "tool_name": tc.get("name"),
                        "result": f"Error: Tool '{tc.get('name')}' not found",
                        "status": "error",
                        "tool_id": f"p{phase}_{tc.get('name')}",
                    })
                    continue

                safe_stream_write(
                    writer,
                    {"event": "status", "data": {
                        "status": "executing",
                        "message": _get_tool_status_msg(actual_name),
                    }},
                    config,
                )
                valid_tc.append(tc)
                phase_tasks.append(
                    ToolExecutor._execute_single_tool(
                        tool=tools_by_name[actual_name],
                        tool_name=actual_name,
                        tool_args=tc.get("args", {}),
                        tool_id=f"p{phase}_{actual_name}",
                        state=state,
                        log=log,
                    )
                )

            if phase_tasks:
                phase_results = await asyncio.gather(*phase_tasks, return_exceptions=True)

                for result, tc in zip(phase_results, valid_tc):
                    if isinstance(result, Exception):
                        log.error(f"❌ Tool execution exception: {result}")
                        continue
                    if not isinstance(result, dict):
                        continue

                    all_tool_results.append(result)
                    actual_name = tc.get("_actual_name", tc.get("name", ""))

                    if result.get("status") == "success":
                        safe_stream_write(
                            writer,
                            {"event": "status", "data": {
                                "status": "executing",
                                "message": _get_tool_done_msg(actual_name),
                            }},
                            config,
                        )
                        result_data = ToolResultExtractor.extract_data_from_result(
                            result.get("result")
                        )
                        if actual_name not in tool_invocation_counts:
                            tool_invocation_counts[actual_name] = 0
                            results_by_tool[actual_name] = result_data
                            log.debug(
                                f"✅ Stored result for {actual_name} "
                                f"(type: {type(result_data).__name__})"
                            )
                        else:
                            tool_invocation_counts[actual_name] += 1
                            suffix_key = f"{actual_name}_{tool_invocation_counts[actual_name] + 1}"
                            results_by_tool[suffix_key] = result_data
                            log.debug(f"✅ Stored result for {suffix_key}")
                    else:
                        err_msg = result.get("error", "Unknown error")
                        safe_stream_write(
                            writer,
                            {"event": "status", "data": {
                                "status": "error",
                                "message": f"Operation failed: {err_msg[:100]}",
                            }},
                            config,
                        )
                        log.debug(f"❌ Skipped storing failed tool: {actual_name}")

            # Tools beyond MAX_PARALLEL_TOOLS go back as remaining for the next phase
            leftover = ready[NodeConfig.MAX_PARALLEL_TOOLS:]
            remaining = leftover + blocked

        return all_tool_results

    @staticmethod
    async def _execute_parallel(
        planned_tools: List[Dict[str, Any]],
        tools_by_name: Dict[str, Any],
        llm: BaseChatModel,
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
    async def _run_tool(tool: object, args: Dict[str, Any]) -> Union[Dict[str, Any], str, Tuple[bool, str], List[Any], None]:
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
    def _process_retrieval_output(result: Union[Dict[str, Any], str, Tuple[bool, str], List[Any], None], state: ChatState, log: logging.Logger) -> str:
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

PLANNER_SYSTEM_PROMPT = """You are the planning brain of an enterprise AI assistant. Your sole output is a JSON execution plan — the exact tools to call, in the exact order, with exact arguments — to fulfill the user's request.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## CORE DECISION TREE — Follow in Strict Order, Every Time
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Evaluate each node in order. Stop at the FIRST node that matches.

```
START
  │
  ▼
[Node 1] Is this a greeting, thanks, or meta-question about the conversation?
  (e.g. "hi", "thanks", "what did we discuss", "summarize our chat")
  → YES: can_answer_directly: true, tools: []   ◄ STOP
  → NO: continue ↓

[Node 2] Is this a WRITE action (create/update/delete/send/post/assign/comment)?
  → YES: Is a REQUIRED parameter missing that only the user can supply?
      → YES: needs_clarification: true, tools: []   ◄ STOP
      → NO: Does the content of this write action need knowledge from internal KB
            (e.g. "send a report about X", "write a summary of our policy on Y",
             "update ticket with info about Z", "email detailed info about W")?
          → YES: Plan retrieval tools ONLY in this cycle. The system will automatically
                 continue to Phase 2 where you will receive the actual retrieved content
                 in your context and can write the email/comment/page body inline using
                 that real data. Do NOT include write tools in this Phase 1 plan.   ◄ STOP
          → NO: continue to select write tool directly ↓

[Node 3] Does the query want to FIND/SEARCH/LEARN about content BY TOPIC, KEYWORD, or MEANING?
  (not by an exact ID/key, not asking purely for current live status)
  Signals: "find tickets about X", "search for issues related to X",
           "[topic] tickets", "[topic] issues", "[topic] bugs", "[topic] pages" — service noun WITHOUT explicit live-filter verb,
           "show me anything about X", "what are the errors/bugs/issues about X",
           "find documents/pages about X", "anything about X", topical/semantic searches,
           "tell me about X", "what is X", "explain X", "show me X about [app topic]",
           "what happened with X", "who worked on X", "find [content] in [app]",
           "search [app] for [topic]", "is there anything about X in [app]" —
           ANY informational/discovery query
  ── CRITICAL: Check the 🧠 KNOWLEDGE & DATA SOURCES section above ──

  ⚠️ SERVICE NOUN RULE: If the query uses a service-specific resource noun
  (tickets, issues, bugs, epics, stories, backlog, pages, spaces, wiki, emails, messages, channels)
  WITHOUT an explicit live-filter verb (my/open/assigned/current/unread/today),
  treat it as a FIND/SEARCH query → fall through to EVALUATE IN ORDER below.

  EVALUATE IN ORDER:

  → Is RETRIEVAL available (any KB or indexed app listed in 📚 INDEXED KNOWLEDGE)
    AND do live SEARCH API tools exist for the relevant app/topic
    (check 🔍 MANDATORY HYBRID SEARCH section — tools with "search" in name)?
      → ⚠️ MANDATORY: Plan BOTH in PARALLEL in the SAME tools array:
          1. `retrieval.search_internal_knowledge` with `"filters": {{}}` (empty, no app filter)
             UNLESS the specific app is listed in "Indexed App Connectors" — then add filters.apps
          2. The matching live search tool for the relevant app
             (e.g. `confluence.search_pages`, `jira.search_issues`, `slack.search_messages`)
        **CRITICAL**: Both tools MUST appear in the same tools array for parallel execution.
        Retrieval searches the KB snapshot (historical/semantic). Live API searches current data.
        The LLM synthesizes the most accurate answer from BOTH sources.   ◄ STOP

  → Is RETRIEVAL available but NO live search API exists for this topic?
      → Use only `retrieval.search_internal_knowledge`   ◄ STOP

  → Is RETRIEVAL NOT available but live search APIs DO exist?
      → Use the matching live search API tool   ◄ STOP

  → NO match (no relevant indexed source, no API): continue ↓

[Node 4] Does the request explicitly ask for CURRENT/LIVE data by a specific filter?
  Signals: "list MY issues", "get issues ASSIGNED to me", "show THIS WEEK's tickets",
           "current sprint", "open PRs", "today's calendar", "unread emails",
           "get issue PA-123" (exact key/ID), live status/metrics/counts
  → YES: Use the matching live API service tool   ◄ STOP
        ⚠️ If the app is ALSO indexed AND the query is ambiguous (could benefit from historical context),
        ADD retrieval in parallel for a richer, more complete answer.
  → NO: continue ↓

[Node 5] Is this an information/knowledge/explanation query about a topic or concept?
  Signals: "what is X", "tell me about X", "explain X", "who is X",
           "how does X work", "our policy on X", "find document about X",
           "what are best practices for X", any vague or ambiguous query
  → YES: Check the 🔍 MANDATORY HYBRID SEARCH section above — are live search APIs available?
      → YES (live search APIs exist + retrieval is available):
             ⚠️ MANDATORY: Use BOTH retrieval AND the matching live API search tool (parallel)   ◄ STOP
             Retrieval gives KB/indexed knowledge; live API gives current data.
             Combining BOTH produces a more accurate and complete answer than either alone.
      → NO (retrieval only, no live search APIs): Use retrieval.search_internal_knowledge only   ◄ STOP
  → NO: continue ↓

[Node 6] Does the query require BOTH knowledge AND a live service action?
  (Hybrid case — e.g. "find the SOP and create a Confluence page from it")
  → YES: Plan retrieval FIRST, then service tool   ◄ STOP
  → NO: continue ↓

[Node 7] DEFAULT — When in doubt: Use retrieval.search_internal_knowledge.
  If a dual-source app is relevant, use BOTH retrieval + live API search.
  Never leave tools: [] unless can_answer_directly or needs_clarification is true.
  ◄ STOP
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## RETRIEVAL IS THE INTELLIGENT DEFAULT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**⚠️ RULE: When in doubt → USE RETRIEVAL + any available live SEARCH APIs in parallel. Never clarify for read/info queries.**
**⚠️ RULE: If tools: [] and needs_clarification: false and can_answer_directly: false → this is INVALID. Add retrieval (or BOTH).**
**⚠️ RULE: If live SEARCH APIs exist (see 🔍 MANDATORY HYBRID SEARCH section) AND query is informational → ALWAYS call them alongside retrieval.**

For the queries below, ALWAYS check the 🔍 MANDATORY HYBRID SEARCH section first.
If live search APIs exist alongside retrieval → use BOTH in parallel. Otherwise → retrieval only.

- "[topic] tickets / [topic] issues / [topic] pages" (service noun, no live filter) → BOTH if live search APIs available; else retrieval
- "Tell me about X" → BOTH if live search APIs available; else retrieval(query="X")
- "What is X" → BOTH if live search APIs available; else retrieval(query="X")
- "Find X" → BOTH if live search APIs available; else retrieval(query="X") — even if vague
- "Find X in [app]" → ALWAYS use the live search API for that app + retrieval in parallel
- "Show me X" where X is a concept/doc → BOTH if live search APIs available; else retrieval
- "Who is X" where X is a person → retrieval (not jira.search_users)
- "Our policy on X" → retrieval (KB content; use BOTH if live search APIs are also available)
- "How does X work" → retrieval (or BOTH if live search APIs are available)
- Ambiguous query with no clear service → BOTH if live search APIs available; else retrieval

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## TOOL TAXONOMY — Two Categories, Both First-Class
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Category A — Internal Knowledge: `retrieval.search_internal_knowledge`
Performs SEMANTIC SEARCH across ALL indexed sources simultaneously — KB documents, app connector snapshots (Jira, Confluence, Slack, Drive, etc.), SOPs, policies, wiki pages, and more.

**USE retrieval when:**
- User wants to FIND or DISCOVER content BY TOPIC, KEYWORD, or MEANING (not by ID)
- User asks "what is X", "tell me about X", "explain X", "who is W", "how does Z work"
- User asks about a topic, policy, process, person, or document
- The relevant app IS LISTED in the 📚 INDEXED KNOWLEDGE section above (check it!)
- Query is about finding/searching across indexed app content (e.g., "find Jira tickets about upload errors" — if Jira is indexed)
- Query is ambiguous — search first, clarify later only if search fails
- You need org context alongside a service action

**⚠️ Retrieval returns formatted TEXT, NOT structured JSON.** Never cascade retrieval output into a service tool needing structured IDs/keys.

### Category B — Connected Service Tools
Live API integrations: Jira, Confluence, Slack, Gmail, Google Drive, etc.

**USE service tools when:**
- User asks for **live/current data** that must reflect the latest state ("list my open Jira issues", "get current sprint tickets", "show unread emails")
- User needs to filter by live/real-time fields (assignee, status, priority, date, current sprint)
- User wants to get a specific item by its ID/key (e.g., "get issue PA-123")
- User wants to **take an action** (create, update, delete, send, post, assign, comment)

**⚠️ Even if a service (e.g. Jira) is INDEXED, use the LIVE API when:**
- The user needs the current/up-to-date state of tickets (not a snapshot)
- The user is filtering by live fields (open issues, assigned to me, this sprint)
- The user wants to perform a write action

**Key distinction — apply this before every tool selection:**
| Query pattern | Tool |
|---|---|
| "[topic] tickets / [topic] issues / [topic] bugs / [topic] pages" (service noun, no live filter) | ⚠️ **MANDATORY: BOTH** retrieval + live search API (parallel) |
| "what is X / tell me about X / explain X" AND live SEARCH APIs available | ⚠️ **MANDATORY: BOTH** retrieval + live search API (parallel) |
| "what is X / tell me about X / explain X" AND NO live SEARCH APIs | retrieval only |
| "find/search [topic] in [app]" AND live search API exists for that app | ⚠️ **MANDATORY: BOTH** retrieval + live search API (parallel in same tools array) |
| "find/search [topic] in [app]" AND no live search API (retrieval only) | retrieval only |
| "list MY / current / open / this-week's [items]" | live API service tool |
| "get [item] by key/ID" (e.g., PA-123) | live API service tool |
| "create / update / delete / send / comment [something]" | live API service write tool |
| "find X" where X is a topic/concept AND live search API available | ⚠️ **MANDATORY: BOTH** retrieval + live search API (parallel in same tools array) |
| "find X" where X is a live status/filter (open, urgent, today) | live API service tool |
| Ambiguous — live search API available | ⚠️ **BOTH** retrieval + live search API (default when uncertain) |
| Ambiguous — no live search API available | retrieval (default) |

### Hybrid — Use Both When Genuinely Required
**⚠️ Parallel search (MANDATORY when app is both indexed AND has live API):**

**⚠️ RETRIEVAL FILTER RULE**: When calling `retrieval.search_internal_knowledge` alongside a
live API tool, do NOT pass `filters.apps` unless that specific app is listed in the
📚 INDEXED KNOWLEDGE → "Indexed App Connectors" section above. If only a KB is indexed,
use `"filters": {{}}` (empty) or omit filters — the KB is always searched with no filter needed.

- "web connector tickets" (Jira live API available, KB indexed) →
    **MUST plan BOTH in same tools array:**
    ```json
    [
      {{"name": "retrieval.search_internal_knowledge", "args": {{"query": "web connector", "filters": {{}}}}}},
      {{"name": "jira.search_issues", "args": {{"jql": "text ~ 'web connector' AND updated >= -90d", "maxResults": 50}}}}
    ]
    ```
    Retrieval finds indexed KB content. Jira API finds live current tickets. LLM synthesizes BOTH.

- "upload failure tickets" (Jira is in Indexed App Connectors + Jira API) →
    **MUST plan BOTH in same tools array:**
    ```json
    [
      {{"name": "retrieval.search_internal_knowledge", "args": {{"query": "upload failure tickets", "filters": {{"apps": ["jira"]}}}}}},
      {{"name": "jira.search_issues", "args": {{"jql": "text ~ 'upload failure' AND updated >= -30d"}}}}
    ]
    ```
    (Only include `filters.apps: ["jira"]` if Jira appears in Indexed App Connectors above)

- "find confluence pages about deployment" (Confluence in Indexed App Connectors + Confluence API) →
    **MUST plan BOTH in same tools array:**
    ```json
    [
      {{"name": "retrieval.search_internal_knowledge", "args": {{"query": "deployment confluence pages", "filters": {{"apps": ["confluence"]}}}}}},
      {{"name": "confluence.search_pages", "args": {{"title": "deployment"}}}}
    ]
    ```

- "find confluence pages about deployment" (only KB indexed, Confluence live API available) →
    **MUST plan BOTH, but retrieval uses NO app filter:**
    ```json
    [
      {{"name": "retrieval.search_internal_knowledge", "args": {{"query": "deployment pages", "filters": {{}}}}}},
      {{"name": "confluence.search_pages", "args": {{"title": "deployment"}}}}
    ]
    ```

**Sequential (retrieval → write):**
- "Find upload failure tickets and add a comment to each" →
    retrieval FIRST (to find tickets), then `jira.add_comment` in Phase 2
- "Find deployment SOP (retrieval) and create a Confluence page from it" →
    retrieval FIRST, then `confluence.create_page` in Phase 2
- Order: retrieval FIRST when you need knowledge to inform a service action or write

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## REFERENCE DATA PRE-CHECK (Run Before Any Tool Selection)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Before planning ANY fetch, scan Reference Data and conversation history for IDs/keys you already have. If found — use them directly and skip the fetch tool.

| Type | Field to use | As parameter |
|---|---|---|
| `confluence_space` | `id` | `space_id` |
| `confluence_page` | `id` | `page_id` |
| `jira_project` | `key` | `project_key` |
| `jira_issue` | `key` | `issue_key` |
| `slack_channel` | `id` | `channel` |

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## PLANNING ALGORITHM — Execute All 4 Steps Every Time
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Step 1 — Decompose
Break the request into distinct subtasks. "List X and post it to Y" = 2 subtasks.

### Step 2 — Classify each subtask using the Decision Tree above

### Step 3 — Select tools by reading descriptions
Read the full tool list under `## AVAILABLE TOOLS`. Select the tool whose description best matches the classified subtask. **Tool selection is description-driven.**

### Step 4 — Order for execution
- **Parallel**: Tools with no data dependencies → list together
- **Sequential**: Tool B needs output from Tool A → list A first, use `{{{{tool_A_name.data.field}}}}` placeholders in B

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## ABSOLUTE RULES (Inviolable — Apply to Every Plan)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**R1 — Service tools win for LIVE/CURRENT data and WRITE actions.**
Use service tools when the user needs real-time data (current state, live filters) or to take a write action. Do NOT substitute retrieval for live data needs.
- ❌ Do NOT use retrieval to list Confluence spaces → use `confluence.get_spaces`
- ❌ Do NOT use retrieval to fetch unread Slack messages → use `slack.get_channel_history`
- ❌ Do NOT use retrieval to get an issue by exact key (PA-123) → use `jira.get_issue`
- ❌ Do NOT use retrieval to list MY open Jira issues by assignee/status → use `jira.search_issues`
- ✅ DO use retrieval to FIND Jira/Confluence/Slack content BY TOPIC when the app IS INDEXED
  (Check the 📚 INDEXED KNOWLEDGE section — if Jira is listed there, retrieval can semantically
   search indexed Jira ticket content, which is often more accurate for topical discovery)

**R1a — Indexed app connector exception (PARALLEL EXECUTION MANDATORY):**
When an app (e.g. Jira, Confluence, Slack) appears in the 📚 INDEXED KNOWLEDGE section AND also has live API tools:
- "[topic] tickets/issues/bugs/pages" (service noun, no live filter) → ⚠️ **MANDATORY: BOTH** `retrieval.search_internal_knowledge` AND the matching live API search tool in the SAME tools array (parallel execution)
- "find tickets/pages/messages ABOUT [topic]" → ⚠️ **MANDATORY: BOTH** `retrieval.search_internal_knowledge` AND the matching live API search tool in the SAME tools array (parallel execution)
- "search for [app] content related to [topic or error or concept]" → ⚠️ **MANDATORY: BOTH** `retrieval.search_internal_knowledge` AND the matching live API search tool in the SAME tools array (parallel execution)
- "get CURRENT/LIVE/OPEN [items] from [app]" → live API service tool (retrieval optional for context)
- "get [item] by specific ID/key" → live API service tool
- "create/update/delete [item] in [app]" → live API write tool

**Why parallel execution is mandatory:**
- Retrieval provides comprehensive semantic search across indexed historical content
- Live API provides current real-time data and exact IDs/keys
- LLM synthesizes BOTH sources for the most accurate, complete answer
- Both execute simultaneously (parallel), saving time
- No information is missed: retrieval finds archived items, API finds current state

**R2 — Retrieval wins for organizational knowledge and information queries.**
If the request is about a topic, concept, policy, process, document, or person — always use retrieval. Never skip retrieval for these just because service tools exist.
- ❌ Do NOT call `jira.search_users` to answer "who is John?" → use retrieval
- ❌ Do NOT skip retrieval for "what is our leave policy?" just because Jira/Confluence tools exist
- ✅ When uncertain whether something is in the knowledge base → search first
- ✅ When the query is topical/semantic and the relevant app is indexed → use retrieval

**R3 — Never use retrieval placeholders with field paths.**
Retrieval returns a plain text string — NOT structured JSON. Never use `{{{{retrieval.xxx.data.results[0].field}}}}` or any field path against a retrieval result.
- ✅ `{{{{retrieval.search_internal_knowledge}}}}` — valid, returns the full text string
- ❌ `{{{{retrieval.search_internal_knowledge.data.results[0].id}}}}` — INVALID, no `.data` object exists
- ❌ `{{{{retrieval.search_internal_knowledge.data.anything}}}}` — INVALID
- For structured IDs (page_id, space_id, accountId) — use the appropriate service tool, never retrieval.

**R4 — Never fabricate structured values.**
If you need an accountId, page_id, space_id, channel ID — read from Reference Data, conversation history, OR call the appropriate lookup service tool. Never invent values.

**R5 — Placeholders only in multi-tool sequential plans.**
`{{{{tool_name.data.field}}}}` is ONLY valid when calling multiple tools in sequence and a downstream tool needs data from an upstream tool. Single-tool plans use actual values only.
- ✅ ONLY use simple numeric indices: `[0]`, `[1]`, `[2]`
- ❌ NEVER use JSONPath filter expressions: `[?(@.key=='value')]`, `[?(expr)]`
- ❌ NEVER use wildcard expressions: `[*]`, `[?]`
- For multiple calls to the same tool: use `tool_name`, `tool_name_2`, `tool_name_3` etc.

**R6 — No instruction text in parameter values.**
Parameters must contain actual values — strings, numbers, IDs, real content. Never write "use the ID from the previous result" as a parameter value.

**R7 — No redundant fetches.**
If Reference Data or conversation history already contains a value, use it directly. Do not add a fetch tool to retrieve data you already have.

**R8 — Clarification only for write actions with genuinely missing required inputs.**
NEVER ask for clarification on read, search, or information queries. If ambiguous → use retrieval or service tools with reasonable defaults.

**R9 — Always produce a non-empty plan unless directly answering or clarifying.**
If `can_answer_directly: false` and `needs_clarification: false`, `tools` must be non-empty. Default to retrieval for any query involving organizational knowledge.

**R10 — Generate complete, final content for write actions; never pipe incompatible formats.**
- **Slack messages**: plain text or Slack mrkdwn (`*bold*`, `_italic_`, `` `code` ``, `\\n`). NEVER pass raw HTML.
- **Confluence pages**: Confluence storage HTML (`<h1>`, `<h2>`, `<p>`, `<ul><li>`, `<pre><code>`, etc.)
- If upstream returns HTML and target needs plain text → YOU translate, not pass through.

**R11 — NEVER tell the user to execute tools. YOU execute tools.**

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## CLARIFICATION RULES (VERY RESTRICTIVE)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Set `needs_clarification: true` ONLY if ALL of these are simultaneously true:
1. User wants to PERFORM a WRITE action (create/update/delete/send/post)
2. A REQUIRED parameter is missing from message AND conversation history
3. Only the user can supply the missing value

**NEVER clarify these — use retrieval instead:**
- "tell me about X" → retrieval(query="X")
- "what is the process" → retrieval(query="process")
- Any query that could be a document name or topic → retrieval
- Any ambiguous query → retrieval, not clarification

**ONLY clarify these:**
- "Create a Jira ticket" (missing: project, summary) → clarify
- "Update the page" (missing: which page, what content) → clarify
- "Send an email" (missing: recipient, body) → clarify

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## AVAILABLE TOOLS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{available_tools}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## CASCADING (Sequential Multi-Step Execution)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Placeholder syntax:** `{{{{tool_name.data.field}}}}`

| Pattern | Syntax example |
|---|---|
| First array item | `{{{{tool.data.results[0].id}}}}` |
| Specific index | `{{{{tool.data.results[2].name}}}}` |
| Nested field | `{{{{tool.data.item.nested.field}}}}` |
| Pagination token | `{{{{tool.data.nextPageToken}}}}` |
| Direct field | `{{{{tool.data.id}}}}` |

**When to cascade:**
- ✅ Tool B requires a structured value (ID, key, token) produced by Tool A
- ✅ Cross-service: fetch from Service A, act on Service B
- ✅ Pagination: user requests "all" and first call produces `nextPageToken`

**Do NOT cascade when:**
- ❌ Value already exists in Reference Data or conversation history → use directly
- ❌ Only one tool is being called → use actual values, not placeholders
- ❌ Upstream search might return empty → check conversation history first

**⚠️ RETRIEVAL + WRITE ACTION — TWO-PHASE MANDATORY RULE:**

When a write action (email, comment, page, etc.) needs content from internal KB retrieval:
- **Phase 1 (THIS cycle)**: Plan retrieval tools ONLY. Do NOT include write tools.
- **Phase 2 (next cycle)**: You will receive the actual retrieved knowledge in your context.
  Write the email/comment/page content inline using that real KB data — factual, grounded content.

The system automatically continues to Phase 2 after retrieval completes.

- ❌ NEVER write email/comment bodies inline at planning time — you don't have the retrieved content yet, so anything you write will be **hallucinated**
- ❌ NEVER put retrieval + write tools in the same plan when write content depends on retrieval
- ✅ Plan retrieval ONLY → system continues → write content inline from actual KB results

**⚠️ RETRIEVAL CASCADING — FIELD PATH RULES:**

Retrieval (`retrieval.search_internal_knowledge`) returns a **plain text string**, NOT structured JSON.
- ❌ NEVER use `{{{{retrieval.search_internal_knowledge.data.results[0].title}}}}` — field paths DO NOT work on retrieval
- ❌ NEVER use `{{{{retrieval.search_internal_knowledge.data.anything}}}}` — there is no `.data` object
- ✅ If you MUST cascade retrieval text into a write tool: `{{{{retrieval.search_internal_knowledge}}}}` (no field path — inserts full retrieved text)
- ✅ PREFERRED: Use two-phase planning (Phase 1: retrieval; Phase 2: write with inline content)

**Example — Single tool (NO placeholders):**
```json
{{
  "tools": [
    {{"name": "confluence.create_page", "args": {{"space_id": "SD", "page_title": "My Page", "page_content": "<h1>My Page</h1><p>Content here.</p>"}}}}
  ]
}}
```

**Example — Cascading (placeholder for space_id):**
```json
{{
  "tools": [
    {{"name": "confluence.get_spaces", "args": {{}}}},
    {{"name": "confluence.create_page", "args": {{"space_id": "{{{{confluence.get_spaces.data.results[0].id}}}}", "page_title": "My Page", "page_content": "<h1>My Page</h1>"}}}}
  ]
}}
```

**Example — Multi-user search then assign:**
```json
{{
  "tools": [
    {{"name": "jira.search_users", "args": {{"query": "Alice"}}}},
    {{"name": "jira.search_users_2", "args": {{"query": "Bob"}}}},
    {{"name": "jira.search_issues", "args": {{"jql": "assignee = {{{{jira.search_users.data.results[0].accountId}}}} AND updated >= -30d"}}}},
    {{"name": "jira.search_issues_2", "args": {{"jql": "assignee = {{{{jira.search_users_2.data.results[0].accountId}}}} AND updated >= -30d"}}}}
  ]
}}
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## PAGINATION HANDLING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

When the user asks for "all", "complete", "everything", or "entire list":
- Plan the first call normally
- Add a cascaded second call: `"nextPageToken": "{{{{tool.data.nextPageToken}}}}"`
- Signals: `isLast: false`, non-null `nextPageToken`, or `hasMore: true` → more pages exist
- Handle automatically — do NOT ask the user about pagination

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## RETRIEVAL QUERY QUALITY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

When calling `retrieval.search_internal_knowledge`:
- Write a concise, targeted query (under 60 characters preferred)
- Use core concept keywords, not the full user question verbatim
- For multi-topic requests, use 2–3 separate retrieval calls with distinct focused queries
- Max 3 retrieval calls per plan unless explicitly required
- Do not duplicate retrieval queries

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## CONTENT GENERATION FOR WRITE ACTIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

When a write tool needs text content, the correct approach depends on **where the content comes from**:

**CASE 1 — Content from conversation history (prior messages):**
→ Extract and write INLINE. The content is already known.

**CASE 2 — Content from retrieval that ALREADY ran (continue/second cycle, context shows retrieved knowledge):**
→ Write INLINE using the ACTUAL knowledge in context. You have real data now — synthesize and compose it fully.

**CASE 3 — Content from retrieval that has NOT yet run (THIS is Phase 1):**
→ ⚠️ MANDATORY: Plan retrieval tools ONLY. Do NOT include write tools in this plan.
→ The system will automatically execute retrieval and continue to Phase 2.
→ In Phase 2 you will have the actual KB content in context and can write inline.
→ Writing inline NOW = hallucination = WRONG because retrieval has not run yet.

**CASE 4 — Content from an upstream service tool (Jira, Confluence, etc.):**
→ Summarize/translate the upstream data; NEVER pipe raw tool output through as-is.

**⚠️ Format Incompatibility — Confluence → Slack (WRONG vs CORRECT):**

❌ WRONG — raw HTML in Slack message:
```json
{{"name": "slack.send_message", "args": {{"channel": "#ch", "message": "{{{{confluence.get_page_content.data.content}}}}"}}}}
```

✅ CORRECT — LLM writes clean summary:
```json
{{"name": "slack.send_message", "args": {{"channel": "#ch", "message": "*Page Summary*\\n\\n• Key point 1\\n• Key point 2"}}}}
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## SERVICE-SPECIFIC RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{jira_guidance}
{confluence_guidance}
{slack_guidance}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## REFERENCE DATA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Reference Data contains IDs and keys from prior turns. Always check BEFORE planning any fetch.

| Type | Field to use | As parameter |
|---|---|---|
| `confluence_space` | `id` | `space_id` |
| `confluence_page` | `id` | `page_id` |
| `jira_project` | `key` | `project_key` |
| `jira_issue` | `key` | `issue_key` |
| `slack_channel` | `id` | `channel` |
| `calendar_event` | `id` | `event_id` |

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## OUTPUT FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Return a **single JSON object**. No markdown code fences. No explanatory text. Valid, parseable JSON only.

{{
  "intent": "one-line description of what the user wants",
  "reasoning": "which Decision Tree node matched and why these tools were selected",
  "can_answer_directly": false,
  "needs_clarification": false,
  "clarifying_question": "",
  "tools": [
    {{"name": "tool.name", "args": {{"param": "value"}}}}
  ]
}}

Output rules:
- `can_answer_directly: true` → `tools` must be `[]`
- `needs_clarification: true` → `tools` must be `[]`, `clarifying_question` must be set
- `can_answer_directly: false` and `needs_clarification: false` → `tools` must be non-empty
- Never produce multiple JSON objects or partial JSON
- Never wrap in markdown code fences
- `reasoning` must state which Decision Tree node matched (e.g., "Node 3 — service noun '[topic] tickets' → BOTH retrieval + jira.search_issues in parallel")"""

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

    system_prompt = PLANNER_SYSTEM_PROMPT.format(
        available_tools=tool_descriptions,
        jira_guidance=jira_guidance,
        confluence_guidance=confluence_guidance,
        slack_guidance=slack_guidance
    )

    # If no knowledge sources are configured, explicitly tell the LLM not to use retrieval
    agent_tools = state.get("tools", []) or []
    has_user_tools = bool(agent_tools)
    has_knowledge = bool(state.get("kb") or state.get("apps") or state.get("agent_knowledge"))

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

    # ── TWO-PHASE ENFORCEMENT ──────────────────────────────────────────────────
    # If the plan has BOTH retrieval.search_internal_knowledge AND write tools,
    # strip the write tools from this cycle. Let retrieval run first; in the
    # continue cycle the planner will see the actual KB content in context and
    # can write grounded email/comment/page content inline — no hallucination.
    #
    # Skip this enforcement if retrieval has already run in a previous iteration
    # (meaning we're in Phase 2 and the planner should proceed with write tools).
    plan_tools = plan.get("tools", [])
    executed_tool_names = state.get("executed_tool_names", [])

    def _is_retrieval_tool(name: str) -> bool:
        n = name.lower()
        return "retrieval" in n or "search_internal_knowledge" in n

    retrieval_already_run = any(_is_retrieval_tool(t) for t in executed_tool_names)
    has_retrieval_in_plan = any(
        _is_retrieval_tool(t.get("name", ""))
        for t in plan_tools if isinstance(t, dict)
    )
    has_write_in_plan = any(
        _is_write_tool(t.get("name", ""))
        for t in plan_tools if isinstance(t, dict)
    )

    if has_retrieval_in_plan and has_write_in_plan and not retrieval_already_run:
        retrieval_tools = [
            t for t in plan_tools
            if isinstance(t, dict) and _is_retrieval_tool(t.get("name", ""))
        ]
        write_tools = [
            t for t in plan_tools
            if isinstance(t, dict) and _is_write_tool(t.get("name", ""))
        ]
        log.info(
            f"⚡ TWO-PHASE PLAN: {len(plan_tools)} total tools detected. "
            f"Deferring {len(write_tools)} write tool(s) to Phase 2 so LLM "
            f"generates content from actual retrieval results (not hallucination). "
            f"Running {len(retrieval_tools)} retrieval tool(s) in Phase 1."
        )
        plan["tools"] = retrieval_tools
        state["execution_plan"] = plan
        state["planned_tool_calls"] = retrieval_tools
        state["pending_tool_calls"] = bool(retrieval_tools)
        # Mark that this is Phase 1 of a genuine two-phase plan (retrieval first,
        # then write action). The reflect node uses this to correctly determine
        # whether a continue is needed vs. the task being read-only.
        state["is_two_phase_plan"] = True
    else:
        # Not a two-phase plan (or Phase 2 is running) — clear the flag.
        state["is_two_phase_plan"] = False
    # ─────────────────────────────────────────────────────────────────────────

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

def _build_conversation_messages(conversations: List[Dict], log: logging.Logger) -> List[Union[HumanMessage, AIMessage]]:
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


def _format_reference_data(all_reference_data: List[Dict], log: logging.Logger) -> str:
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
    calendar_events = [d for d in all_reference_data if d.get("type") == "calendar_event"]

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

    if calendar_events:
        parts = [
            f"{item.get('name', item.get('id', '?'))} (event_id={item.get('id', '?')})"
            for item in calendar_events[:max_items]
        ]
        lines.append(f"**Google Calendar Events** (use `event_id` for update_calendar_event/delete_calendar_event): {', '.join(parts)}")

    log.debug(
        f"📎 Reference data: {len(spaces)} spaces, {len(pages)} pages, "
        f"{len(projects)} jira projects, {len(issues)} jira issues, "
        f"{len(channels)} slack channels, {len(msg_timestamps)} slack ts, "
        f"{len(calendar_events)} calendar events"
    )

    return "\n".join(lines)


def _build_planner_messages(state: ChatState, query: str, log: logging.Logger) -> List[Union[HumanMessage, AIMessage, SystemMessage]]:
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
    if user_context:
        # Combine query and user context
        query_content = f"{query}\n\n{user_context}"
    else:
        query_content = query

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
    # Section 3 — Duplicate-prevention guard for write/action tools
    # ══════════════════════════════════════════════════════════════════════════
    completed_writes = [
        r.get("tool_name", "unknown")
        for r in tool_results
        if r.get("status") == "success" and _is_write_tool(r.get("tool_name", ""))
    ]
    if completed_writes:
        parts.append(
            "⚠️ ALREADY COMPLETED — DO NOT REPEAT: The following tools already "
            "succeeded. Planning them again will create duplicates:\n" +
            "\n".join(f"  ✅ {t}" for t in completed_writes) +
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
    messages: List[Union[HumanMessage, AIMessage, SystemMessage]],
    state: ChatState,
    log: logging.Logger,
    query: str,
    writer: Optional[StreamWriter] = None,
    config: Optional[RunnableConfig] = None
) -> Dict[str, Any]:
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
            llm_messages = [SystemMessage(content=system_prompt)] + messages

            # Start periodic status updates task if writer is available
            update_task = None
            if writer and config:
                async def send_periodic_updates() -> None:
                    """Send periodic status updates during long planning operations"""
                    try:
                        await asyncio.sleep(3)  # Wait 3 seconds before first update
                        safe_stream_write(writer, {
                            "event": "status",
                            "data": {"status": "planning", "message": "Still analyzing... this may take a moment"}
                        }, config)
                        await asyncio.sleep(5)  # Wait 5 more seconds
                        safe_stream_write(writer, {
                            "event": "status",
                            "data": {"status": "planning", "message": "Reviewing available tools and planning steps..."}
                        }, config)
                    except asyncio.CancelledError:
                        pass

                update_task = asyncio.create_task(send_periodic_updates())

            try:
                # Call LLM with full conversation context as messages
                response = await asyncio.wait_for(
                    llm.ainvoke(llm_messages, config=invoke_config),
                    timeout=NodeConfig.PLANNER_TIMEOUT_SECONDS
                )
            finally:
                # Cancel update task if it's still running
                if update_task and not update_task.done():
                    update_task.cancel()
                    try:
                        await update_task
                    except asyncio.CancelledError:
                        pass

            # Parse response
            plan = _parse_planner_response(
                response.content if hasattr(response, 'content') else str(response),
                log
            )

            # Validate tools
            tools = plan.get('tools', [])

            # Fix empty retrieval queries in fallback plans
            for tool in tools:
                if "retrieval" in tool.get("name", "").lower():
                    if not tool.get("args", {}).get("query", "").strip():
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
                        if isinstance(test_plan, dict):
                            # Prefer objects with "tools" field, but accept any valid dict
                            if "tools" in test_plan or not found_valid:
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


def _create_fallback_plan(query: str, state: "ChatState | None" = None) -> Dict[str, Any]:
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
        has_knowledge = bool(
            state.get("agent_knowledge")
            or state.get("kb")
            or state.get("apps")
        )

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


def _has_slack_tools(state: ChatState) -> bool:
    """Check if Slack tools available"""
    agent_toolsets = state.get("agent_toolsets", [])
    return any(isinstance(ts, dict) and "slack" in ts.get("name", "").lower() for ts in agent_toolsets)

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
    indexed_apps: list[dict] = []   # {"label": str, "type_key": str}

    for k in agent_knowledge:
        if not isinstance(k, dict):
            continue
        name     = k.get("displayName") or k.get("name") or ""
        ktype    = (k.get("type") or "").strip()
        ktype_up = ktype.upper()

        if ktype_up == "KB":
            kb_sources.append(name or "Knowledge Base")
        else:
            # Normalise to lowercase single-word key (e.g. "DRIVE WORKSPACE" → "drive")
            type_key = ktype.lower().split()[0] if ktype else ""
            label    = name or type_key.capitalize() or "App Connector"
            indexed_apps.append({"label": label, "type_key": type_key})

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
        tool_names = []
        for t in ts_tools:
            if isinstance(t, dict):
                tool_names.append(
                    t.get("fullName") or
                    f"{ts_key}.{t.get('toolName') or t.get('name', '')}"
                )
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
            for kb in kb_sources:
                lines.append(f"  - 📄 {kb}")

        if indexed_apps:
            lines.append(
                "\n**Indexed App Connectors** (text is searchable via retrieval):\n"
                "  ⚠️  Only these app names are valid in `filters.apps` for retrieval:"
            )
            for app in indexed_apps:
                lines.append(f"  - 🔗 `{app['type_key']}` ({app['label']})")
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
            "\n### 🔍 MANDATORY HYBRID SEARCH — when to combine retrieval + live search APIs"
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
_tool_description_cache: Dict[str, str] = {}


def _get_cached_tool_descriptions(state: ChatState, log: logging.Logger) -> str:
    """Get tool descriptions with caching"""
    org_id = state.get("org_id", "default")
    agent_toolsets = state.get("agent_toolsets", [])
    llm = state.get("llm")

    has_knowledge = bool(state.get("kb") or state.get("apps") or state.get("agent_knowledge"))

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


def _extract_parameters_from_schema(schema: Union[Dict[str, Any], type], log: logging.Logger) -> Dict[str, Dict[str, Any]]:
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
            desc_text = description[:MAX_TOOL_DESCRIPTION_LENGTH] if len(description) > MAX_TOOL_DESCRIPTION_LENGTH else description
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

    log.info(f"📊 Tool results: {len(successful)} ✓, {len(failed)} ✗")

    # Log details for debugging
    for r in successful:
        log.info(f"  ✅ {r.get('tool_name')}")
    for r in failed:
        log.info(f"  ❌ {r.get('tool_name')}: {str(r.get('result', ''))[:100]}")

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
    intent_to_tool_segment: List[tuple] = [
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


def _is_write_tool(tool_name: str) -> bool:
    """
    Return True if the tool performs a write / action / side-effect operation.

    Detection is done by inspecting the verb prefix of the tool's action segment
    (the part after the service prefix, e.g. "slack.", "jira.", "confluence.").
    Prefix matching is reliable because all service tools follow the convention
    <service>.<verb>_<object> (e.g. slack.send_message, jira.create_issue).
    """
    name = tool_name.lower()
    # Strip service prefix (e.g. "slack.", "jira.", "confluence.", "retrieval.")
    if "." in name:
        name = name.split(".", 1)[1]
    _WRITE_PREFIXES = (
        "create_", "update_", "delete_", "add_", "send_", "reply_",
        "set_", "assign_", "transition_", "publish_", "post_",
        "remove_", "move_", "archive_", "upload_", "comment_",
        "edit_", "modify_", "write_", "submit_",
    )
    return any(name.startswith(p) for p in _WRITE_PREFIXES)


def _is_read_tool(tool_name: str) -> bool:
    """
    Return True if the tool is a read-only / information-gathering operation.
    """
    name = tool_name.lower()
    if "." in name:
        name = name.split(".", 1)[1]
    _READ_PREFIXES = (
        "get_", "search_", "list_", "fetch_", "retrieve_",
        "find_", "query_", "read_",
    )
    return any(name.startswith(p) for p in _READ_PREFIXES)


# Compiled regex for detecting write-action intent in the user query.
# Design: match ONLY when the verb is used as an action command, not a noun/modifier.
# - We require the write verb to appear either at the sentence start (after optional
#   polite preamble) OR after a conjunction ("and", "then", "also").
# - Removed "upload", "message", "write", "comment", "add" from the top-level match
#   because they appear routinely as nouns/adjectives in search queries
#   (e.g. "upload failure tickets", "comment count", "write permission error").
# - "set status" is kept as a phrase since it's always an action.
_WRITE_INTENT_RE = re.compile(
    r"(?:"
    # Pattern A: verb at start of sentence (optional polite prefix)
    r"^(?:please\s+|can\s+you\s+|could\s+you\s+|i\s+(?:need|want|would\s+like)\s+(?:you\s+)?to\s+)?"
    r"\b(send|reply|create|update|publish|notify|assign|post|set\s+status)\b"
    r"|"
    # Pattern B: verb after a conjunction (e.g. "find X and then send email")
    r"\b(?:and|then|also|after\s+that)\b\s+\b(send|reply|create|update|publish|notify|assign|post|set\s+status)\b"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

# Additional fallback: explicit write-action verbs that are unambiguous anywhere
_UNAMBIGUOUS_WRITE_RE = re.compile(
    r"\b(add\s+comment|add\s+a\s+comment|leave\s+a\s+comment|write\s+(?:an?\s+)?email|"
    r"upload\s+(?:the\s+)?file|comment\s+on\s+(?:the\s+|this\s+)?(?:ticket|issue|pr|pull\s+request))\b",
    re.IGNORECASE,
)


def _check_if_task_needs_continue(
    query: str,
    executed_tools: List[str],
    tool_results: List[Dict[str, Any]],
    log: logging.Logger,
    state: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Determine whether the agent needs another planning cycle to complete the task.

    Logic (in order):
    1. If at least one write/action tool already executed → task action phase done.
    2. If this is Phase 1 of a genuine two-phase plan (retrieval before write) →
       continue so the planner can execute the write action in Phase 2.
    3. If the planner planned ONLY retrieval tools (no write plan at all) →
       this is a read-only task regardless of what the regex detects. Return False.
    4. If the query clearly signals a write/action intent (via tightened regex)
       and no write tool has run yet → continue.
    5. Default → False (task complete or read-only).
    """
    query_lower = (query or "").lower()
    state = state or {}

    # ── 1. Was any write/action tool executed? ────────────────────────────────
    action_completed = any(_is_write_tool(t) for t in executed_tools)
    if action_completed:
        log.debug(
            "Task complete: write/action tool already executed → no continue needed. "
            f"Executed: {executed_tools}"
        )
        return False

    # ── 2. Is this Phase 1 of a genuine two-phase plan? ─────────────────────
    # The planner_node sets is_two_phase_plan=True when it strips write tools
    # from the plan so that retrieval can run first. In that case we MUST continue
    # to execute the deferred write tools in Phase 2.
    if state.get("is_two_phase_plan"):
        log.debug(
            "Task incomplete: two-phase plan in Phase 1 — write tools deferred to Phase 2. "
            f"Executed: {executed_tools}"
        )
        return True

    # ── 3. Did the planner choose ONLY retrieval tools (read-only signal)? ───
    # planned_tool_calls reflects what the planner actually decided to do.
    # If it contains ONLY retrieval tools, the LLM determined this is a read-only
    # task — trust it and don't force a continuation based on regex alone.
    planned_tools = state.get("planned_tool_calls", []) or []
    if planned_tools:
        all_planned_are_retrieval = all(
            _is_retrieval_tool(t.get("name", "")) if isinstance(t, dict)
            else "retrieval" in str(t).lower()
            for t in planned_tools
        )
        if all_planned_are_retrieval:
            log.debug(
                "Task complete: planner only planned retrieval tools → read-only task. "
                f"Executed: {executed_tools}"
            )
            return False

    # ── 4. Does the query signal a write/action intent (tightened regex)? ────
    if _WRITE_INTENT_RE.search(query_lower) or _UNAMBIGUOUS_WRITE_RE.search(query_lower):
        log.debug(
            "Task incomplete: query indicates a write/action intent but no action "
            f"tool has executed yet. Executed: {executed_tools}"
        )
        return True

    # ── 5. Default: task is complete (read-only or already handled) ───────────
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
    final_results: List[Dict[str, Any]],
    log: logging.Logger
) -> List[Dict[str, Any]]:
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
    seen_blocks: Dict[tuple, Any] = {}
    # Track the best score seen for each document (used for document ordering)
    doc_best_score: Dict[str, float] = {}
    # Track the earliest position each document appeared in the results list
    doc_first_position: Dict[str, int] = {}

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

    def sort_key(x: Dict[str, Any]) -> tuple:
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
    query = state.get("query", "")

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
        and "knowledge" not in r.get("tool_name", "").lower()
    ]
    failed_results = [r for r in tool_results if r.get("status") == "error"]

    if non_retrieval_results or (failed_results and not any(r.get("status") == "success" for r in tool_results)):
        # Build context for API tool results.
        # When qna_message_content is set, retrieval blocks are already embedded in the
        # user message — pass [] to avoid duplication but set has_retrieval_in_context=True
        # so the LLM is instructed to use MODE 3 (inline citations + referenceData).
        qna_has_retrieval = bool(state.get("qna_message_content"))
        context = _build_tool_results_context(
            tool_results,
            [] if qna_has_retrieval else final_results,
            has_retrieval_in_context=qna_has_retrieval,
        )
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
            try:
                # Try to get context length from LLM config if available
                # This is a fallback - ideally it should be stored in state
                context_length = DEFAULT_CONTEXT_LENGTH
            except Exception:
                pass

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
                            normalize_citations_and_chunks_for_agent as _ncc_agent,  # noqa: PLC0415
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
    config: RunnableConfig
) -> str:
    """Generate direct response with full conversation context as LangChain messages"""
    query = state.get("query", "")
    previous = state.get("previous_conversations", [])

    # Build messages with full conversation history (same as planner)
    messages = []

    # System message
    user_context = _format_user_context(state)

    # If the agent has no knowledge and no tools, use a specialized system prompt that
    # always steers the LLM to guide the user to configure the agent for org-specific queries.
    if state.get("agent_not_configured_hint"):
        system_content = (
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
        system_content = "You are a helpful, friendly AI assistant. Respond naturally and concisely."
        if user_context:
            system_content += "\n\nIMPORTANT: When user asks about themselves, use provided info DIRECTLY."

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


def _build_tool_results_context(
    tool_results: List[Dict],
    final_results: List[Dict],
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
        parts.append("Transform data into professional markdown.\n")
        parts.append("Store IDs/keys in referenceData.\n\n")

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

    parts.append(
        "\n## 🔗 LINK REQUIREMENTS (MANDATORY)\n\n"
        "For EVERY item from an external service, you MUST include a clickable markdown link:\n"
        "- **Jira issue**: `[KEY-123](url)` — use the `url` field if present, else `[KEY-123]` with text\n"
        "- **Confluence page/space**: `[Page Title](url)` — use the `url` field from the result\n"
        "- **Google Drive file**: `[filename](webViewLink)` — use the `url` / `webViewLink` field\n"
        "- **Gmail message**: `[subject/id](url)` — use the `url` field (gmail.google.com link)\n"
        "- **Slack channel**: include `#channel-name` and link if `url` is present\n"
        "If no URL is available, still mention the item name/key so the user can find it.\n"
        "Include ALL links in the referenceData array as `{\"name\": ..., \"url\": ..., \"type\": ...}`.\n\n"
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
    # Start with agent instructions if provided
    agent_instructions = state.get("instructions")
    instructions_prefix = ""
    if agent_instructions and agent_instructions.strip():
        instructions_prefix = f"## Agent Instructions\n{agent_instructions.strip()}\n\n"

    base_prompt = instructions_prefix + """You are an intelligent AI assistant that can use tools to help users.

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
    """
    Extract reference data (IDs, keys, timestamps) from tool results so they can
    be surfaced to the planner for follow-up queries.

    Each returned dict has at minimum: {"type": str, "name": str} and usually "id"
    and/or "key" fields that the planner can reference directly.
    """
    reference_data = []
    tn_lower = tool_name.lower()

    try:
        # ── Normalise result to a Python object ─────────────────────────────
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (json.JSONDecodeError, ValueError):
                return reference_data

        # Unwrap tuple format (success, data)
        # Tools return (bool, str) tuple - extract the data part
        if isinstance(result, tuple) and len(result) == 2:  # noqa: PLR2004
            result = result[1]
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except (json.JSONDecodeError, ValueError):
                    return reference_data

        # ── Confluence spaces ────────────────────────────────────────────────
        # confluence.get_spaces → {"data": {"results": [{id, key, name, _links, ...}]}}
        if "confluence" in tn_lower and "space" in tn_lower:
            if isinstance(result, dict):
                data = result.get("data", {})
                spaces_list: List = []
                # Extract base URL from the root _links (Confluence v2 API)
                confluence_base_url = ""
                if isinstance(data, dict):
                    root_links = data.get("_links", {})
                    if isinstance(root_links, dict):
                        confluence_base_url = root_links.get("base", "")
                    spaces_list = data.get("results", [])
                elif isinstance(data, list):
                    spaces_list = data
                for space in spaces_list:
                    if not isinstance(space, dict):
                        continue
                    # Confluence v2 API may return id as integer or string
                    raw_id = space.get("id")
                    space_id = str(raw_id).strip() if raw_id is not None else ""
                    space_key = space.get("key", "")
                    space_name = space.get("name", "")
                    # Build web URL from _links.webui
                    space_url = ""
                    space_links = space.get("_links", {})
                    if isinstance(space_links, dict):
                        webui = space_links.get("webui", "")
                        if webui and confluence_base_url:
                            space_url = f"{confluence_base_url.rstrip('/')}{webui}"
                        elif webui:
                            space_url = webui
                    if space_id or space_key:
                        ref_entry: Dict[str, Any] = {
                            "name": space_name,
                            "id": space_id,
                            "key": space_key,
                            "type": "confluence_space",
                        }
                        if space_url:
                            ref_entry["url"] = space_url
                        reference_data.append(ref_entry)
            return reference_data

        # ── Confluence pages ─────────────────────────────────────────────────
        # confluence.get_pages_in_space / search_pages / get_page_content
        if "confluence" in tn_lower and any(k in tn_lower for k in ("page", "search", "child")):
            if isinstance(result, dict):
                data = result.get("data", {})
                pages_list: List = []
                # Extract base URL from the root _links (Confluence v2 API)
                confluence_base_url = ""
                if isinstance(data, dict):
                    root_links = data.get("_links", {})
                    if isinstance(root_links, dict):
                        confluence_base_url = root_links.get("base", "")
                    pages_list = data.get("results", [])
                    # Single-page result: get_page_content embeds metadata in data
                    if not pages_list and "id" in data:
                        pages_list = [data]
                elif isinstance(data, list):
                    pages_list = data
                for page in pages_list:
                    if not isinstance(page, dict):
                        continue
                    raw_id = page.get("id")
                    page_id = str(raw_id).strip() if raw_id is not None else ""
                    page_title = page.get("title", "") or page.get("name", "")
                    space_key = ""
                    if isinstance(page.get("space"), dict):
                        space_key = page["space"].get("key", "")
                    if page_id:
                        reference_data.append({
                            "name": page_title,
                            "id": page_id,
                            "key": space_key,
                            "type": "confluence_page",
                        })
            return reference_data

        # ── Slack channels ───────────────────────────────────────────────────
        if "slack" in tn_lower and "channel" in tn_lower:
            if isinstance(result, dict):
                data = result.get("data", {})
                channels_list: List = []
                if isinstance(data, dict):
                    channels_list = data.get("channels", data.get("results", []))
                elif isinstance(data, list):
                    channels_list = data
                for ch in channels_list:
                    if not isinstance(ch, dict):
                        continue
                    ch_id = ch.get("id", "")
                    ch_name = ch.get("name", "")
                    # Slack deep link for channels (workspace-agnostic)
                    ch_url = ch.get("permalink") or ch.get("url", "")
                    if ch_id:
                        ref_entry = {
                            "name": f"#{ch_name}" if ch_name else ch_id,
                            "id": ch_id,
                            "type": "slack_channel",
                        }
                        if ch_url:
                            ref_entry["url"] = ch_url
                        reference_data.append(ref_entry)
            return reference_data

        # ── Slack messages / threads ─────────────────────────────────────────
        # send_message / reply_to_message return the message timestamp ("ts")
        # which is needed for threading (reply_to_message thread_ts).
        if "slack" in tn_lower and any(k in tn_lower for k in ("message", "reply", "send")):
            if isinstance(result, dict):
                data = result.get("data", {})
                if not isinstance(data, dict):
                    data = {}
                ts = data.get("ts") or result.get("ts", "")
                channel = data.get("channel") or result.get("channel", "")
                permalink = data.get("permalink") or result.get("permalink", "")
                if ts:
                    msg_ref: Dict[str, Any] = {
                        "name": "Posted message timestamp",
                        "id": str(ts),
                        "type": "slack_message_ts",
                    }
                    if permalink:
                        msg_ref["url"] = permalink
                    reference_data.append(msg_ref)
                if channel:
                    reference_data.append({
                        "name": channel,
                        "id": channel,
                        "type": "slack_channel",
                    })
            return reference_data

        # ── Google Calendar events ───────────────────────────────────────────
        # calendar.create_calendar_event / create_meet_link / update_calendar_event
        # All return {"event_id": str, "event_title": str, ...}
        if "calendar" in tn_lower and isinstance(result, dict):
            event_id = result.get("event_id", "")
            event_title = result.get("event_title", "")
            if event_id:
                reference_data.append({
                    "name": event_title or event_id,
                    "id": str(event_id),
                    "type": "calendar_event",
                })
            return reference_data

        # ── Jira ─────────────────────────────────────────────────────────────
        # Only process Jira data when the tool is actually a Jira tool.
        # Without this guard, Confluence results that happen to contain a "key"
        # field would be misclassified as Jira entities.
        if "jira" in tn_lower and isinstance(result, dict):
            data = result.get("data", {})

            # Issues list: {"data": {"issues": [...]}}
            if isinstance(data, dict):
                issues = data.get("issues", [])
                if isinstance(issues, list):
                    for issue in issues:
                        if not isinstance(issue, dict):
                            continue
                        issue_id = issue.get("id", "")
                        issue_key = issue.get("key", "")
                        issue_url = issue.get("url", "")
                        if issue_key or issue_id:
                            ref: Dict[str, Any] = {
                                "name": issue.get("summary", ""),
                                "key": issue_key,
                                "type": "jira_issue",
                            }
                            if issue_id:
                                ref["id"] = str(issue_id)
                            if issue_url:
                                ref["url"] = issue_url
                            reference_data.append(ref)

            # Single issue at data level (get_issue / create_issue response)
            if isinstance(data, dict) and "key" in data:
                issue_key = data.get("key", "")
                issue_id = data.get("id", "")
                issue_url = data.get("url", "")
                if issue_key or issue_id:
                    ref = {
                        "name": data.get("summary", ""),
                        "key": issue_key,
                        "type": "jira_issue",
                    }
                    if issue_id:
                        ref["id"] = str(issue_id)
                    if issue_url:
                        ref["url"] = issue_url
                    reference_data.append(ref)

            # Projects list: {"data": [...]}
            if isinstance(data, list):
                for project in data:
                    if not isinstance(project, dict):
                        continue
                    project_id = project.get("id", "")
                    project_key = project.get("key", "")
                    if project_key or project_id:
                        ref = {
                            "name": project.get("name", ""),
                            "key": project_key,
                            "type": "jira_project",
                        }
                        if project_id:
                            ref["id"] = str(project_id)
                        reference_data.append(ref)

            # Direct issue / project at result top-level (e.g. create_issue response)
            if "key" in result:
                item_id = result.get("id", "")
                item_key = result.get("key", "")
                item_url = result.get("url", "")
                if item_key or item_id:
                    ref = {
                        "name": result.get("summary") or result.get("name", ""),
                        "key": item_key,
                        "type": "jira_issue" if "summary" in result else "jira_project",
                    }
                    if item_id:
                        ref["id"] = str(item_id)
                    if item_url:
                        ref["url"] = item_url
                    reference_data.append(ref)

        elif "jira" in tn_lower and isinstance(result, list):
            for item in result:
                if not isinstance(item, dict) or "key" not in item:
                    continue
                item_id = item.get("id", "")
                item_key = item.get("key", "")
                item_url = item.get("url", "")
                if item_key or item_id:
                    ref = {
                        "name": item.get("summary") or item.get("name", ""),
                        "key": item_key,
                        "type": "jira_issue" if "summary" in item else "jira_project",
                    }
                    if item_id:
                        ref["id"] = str(item_id)
                    if item_url:
                        ref["url"] = item_url
                    reference_data.append(ref)

    except Exception as e:
        logger.debug(f"Error extracting reference data from {tool_name}: {e}")

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
    "CascadingExecutor",
    "react_agent_node",
]
